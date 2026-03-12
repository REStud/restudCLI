"""
Parser and renderer for Andrea Markup Language (.aml) report files.

AML format rules:
  - [metadata] section uses key = "value" or key = value (TOML-like)
  - [requests] and [recommendations] sections contain items starting with '-'
  - Each item runs from its leading '-' until the next '-' or next section header
  - Lines starting with '#' are comments and are ignored everywhere
  - Tag snippets (*tagname) anywhere in item text are replaced with the
    corresponding text from base-snippets.toml
"""

import re
import toml
import jinja2
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _strip_comments(lines: List[str]) -> List[str]:
    """Remove lines that start with # (after optional whitespace)."""
    return [l for l in lines if not re.match(r'^\s*#', l)]


def _parse_metadata(lines: List[str]) -> Dict[str, Any]:
    """Parse the [metadata] block as TOML key=value pairs."""
    text = '\n'.join(lines)
    try:
        return toml.loads(text)
    except Exception as e:
        raise ValueError(f"Error parsing [metadata] section: {e}")


def _parse_verbatim(lines: List[str]) -> str:
    """Join lines verbatim for sections like [beginning] and [endmessage]."""
    return '\n'.join(lines).strip()


def _parse_items(lines: List[str]) -> List[str]:
    """
    Parse a list of text items from lines.

    Each item starts with a line whose first non-whitespace chars are '-'.
    Subsequent lines (not starting with '-') are continuation of the same item.
    The dash itself and any immediately following space are stripped from the
    first line of each item; continuation lines are kept verbatim.
    """
    items = []
    current: Optional[List[str]] = None

    for line in lines:
        if re.match(r'^\s*-', line):
            if current is not None:
                items.append('\n'.join(current).strip())
            # Strip leading dash (and one optional space) from the first line
            first = re.sub(r'^\s*-\s?', '', line)
            current = [first] if first.strip() else []
        else:
            if current is not None:
                current.append(line)
            # Lines before the first dash are ignored

    if current is not None:
        items.append('\n'.join(current).strip())

    return [item for item in items if item]


def parse_aml(report_path: str) -> Dict[str, Any]:
    """
    Parse an .aml file and return a dict with keys:
      metadata, requests, recommendations
    """
    with open(report_path, 'r', encoding='utf-8') as f:
        raw_lines = f.readlines()

    # Preserve line endings stripped
    lines = [l.rstrip('\n') for l in raw_lines]

    # Remove comment lines
    lines = _strip_comments(lines)

    # Split into sections
    sections: Dict[str, List[str]] = {}
    current_section: Optional[str] = None

    section_re = re.compile(r'^\[(\w+)\]\s*$')

    for line in lines:
        m = section_re.match(line)
        if m:
            current_section = m.group(1)
            sections[current_section] = []
        elif current_section is not None:
            sections[current_section].append(line)

    if 'metadata' not in sections:
        raise ValueError("Missing [metadata] section in .aml file")

    metadata = _parse_metadata(sections['metadata'])
    beginning = _parse_verbatim(sections.get('beginning', []))
    requests = _parse_items(sections.get('requests', []))
    recommendations = _parse_items(sections.get('recommendations', []))
    endmessage = _parse_verbatim(sections.get('endmessage', []))

    return {
        'metadata': metadata,
        'beginning': beginning,
        'requests': requests,
        'recommendations': recommendations,
        'endmessage': endmessage,
    }


# ---------------------------------------------------------------------------
# Snippet substitution
# ---------------------------------------------------------------------------

def _substitute_snippets(text: str, snippets: Dict[str, str]) -> str:
    """Replace *tagname occurrences in text with snippet values."""
    def replacer(m):
        key = m.group(0)  # e.g. "*DAS"
        return snippets.get(key, key)
    # Match * followed by word characters
    return re.sub(r'\*\w+', replacer, text)


def substitute_snippets_in_data(data: Dict[str, Any], snippets: Dict[str, str]) -> Dict[str, Any]:
    """Apply snippet substitution to all text fields."""
    data['beginning'] = _substitute_snippets(data.get('beginning', ''), snippets)
    data['requests'] = [_substitute_snippets(r, snippets) for r in data.get('requests', [])]
    data['recommendations'] = [_substitute_snippets(r, snippets) for r in data.get('recommendations', [])]
    data['endmessage'] = _substitute_snippets(data.get('endmessage', ''), snippets)
    return data


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class AMLReportRenderer:
    """Render .aml reports using the same Jinja2 templates as the TOML renderer."""

    def __init__(self, templates_dir: str):
        self.templates_dir = Path(templates_dir)
        self.loader = jinja2.FileSystemLoader(str(self.templates_dir))
        self.env = jinja2.Environment(
            loader=self.loader,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.globals['chr'] = chr

    def load_snippets(self, snippets_path: Optional[str] = None) -> Dict[str, str]:
        if snippets_path is None:
            snippets_path = self.templates_dir / 'base-snippets.toml'
        if not os.path.exists(snippets_path):
            return {}
        with open(snippets_path, 'r', encoding='utf-8') as f:
            data = toml.load(f)
        # Support both flat {"*tag": text} and grouped {"group": {"*tag": text}}
        raw = data.get('snippets', {})
        flat: Dict[str, str] = {}
        for v in raw.values():
            if isinstance(v, dict):
                flat.update(v)
            else:
                flat[v] = v  # shouldn't happen in normal use
        return flat

    def generate_report(
        self,
        report_path: str,
        template_name: str,
        snippets_path: Optional[str] = None,
        extra_context: Optional[dict] = None,
    ) -> str:
        snippets = self.load_snippets(snippets_path)
        data = parse_aml(report_path)
        data = substitute_snippets_in_data(data, snippets)

        # Flatten metadata into top-level context (templates use {{ metadata.X }})
        context = {
            'metadata': data['metadata'],
            'beginning': data.get('beginning', ''),
            'requests': data['requests'],
            'recommendations': data['recommendations'],
            'endmessage': data.get('endmessage', ''),
            'comments': [],   # no DCAS rules in AML
            'snippets': snippets,
        }
        if extra_context:
            context.update(extra_context)

        template = self.env.get_template(template_name)
        return template.render(**context)

    def validate_aml(self, report_path: str) -> Tuple[bool, str]:
        try:
            data = parse_aml(report_path)
            meta = data.get('metadata', {})
            for field in ('manuscript_id', 'salutation', 'title', 'email'):
                if field not in meta:
                    return False, f"Missing required metadata field: '{field}'"
            return True, "Valid"
        except Exception as e:
            return False, str(e)
