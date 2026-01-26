"""
Jinja2-based report rendering for TOML format.
Replaces YAML-based rendering with flexible Jinja2 templating.
"""

import os
import toml
import jinja2
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path


class ReportRenderer:
    """Render TOML reports using Jinja2 templates"""

    def __init__(self, templates_dir: str):
        """Initialize the renderer with a templates directory"""
        self.templates_dir = Path(templates_dir)
        self.loader = jinja2.FileSystemLoader(str(self.templates_dir))
        self.env = jinja2.Environment(
            loader=self.loader,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Add custom filters and globals
        self.env.globals['chr'] = chr

    def load_toml_report(self, report_path: str) -> Dict[str, Any]:
        """Load a TOML report file"""
        with open(report_path, 'r', encoding='utf-8') as f:
            data = toml.load(f)

        # If requests/recommendations are in a [root] section, promote them to top level
        if 'root' in data:
            for key in ['requests', 'recommendations']:
                if key in data['root']:
                    data[key] = data['root'][key]
            # Remove the root section
            del data['root']

        return data

    def load_snippets(self, snippets_path: Optional[str] = None) -> Dict[str, str]:
        """Load reusable text snippets from TOML file"""
        if snippets_path is None:
            snippets_path = self.templates_dir / 'base-snippets.toml'

        if not os.path.exists(snippets_path):
            return {}

        with open(snippets_path, 'r', encoding='utf-8') as f:
            data = toml.load(f)

        return data.get('snippets', {})

    def filter_empty_sections(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove empty sections from report"""
        filtered = {}

        for key, value in data.items():
            if key == 'metadata':
                filtered[key] = value
            elif isinstance(value, list):
                # Only include non-empty lists
                if value:
                    filtered[key] = value
            elif value:
                filtered[key] = value

        return filtered

    def render_template(
        self,
        template_name: str,
        context: Dict[str, Any],
        snippets: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Render a Jinja2 template with context

        Args:
            template_name: Name of the template file (e.g., 'response1.jinja2')
            context: Dictionary of variables for template
            snippets: Optional snippets dictionary to make available in template

        Returns:
            Rendered template string
        """
        template = self.env.get_template(template_name)

        # Add snippets to context if provided
        if snippets:
            context['snippets'] = snippets

        return template.render(**context)

    def substitute_tags(self, data: Dict[str, Any], snippets: Dict[str, str]) -> Dict[str, Any]:
        """
        Substitute tag references (like 'cite_data') with snippet values in comments

        Args:
            data: Report data dictionary
            snippets: Snippets dictionary with tag values

        Returns:
            Data with tags substituted in comments
        """
        if 'dcas_rules' not in data:
            return data

        for rule in data.get('dcas_rules', []):
            if 'comments' in rule and rule['comments']:
                new_comments = []
                for comment in rule['comments']:
                    # Check if comment is a tag reference (exists in snippets)
                    if comment in snippets:
                        new_comments.append(snippets[comment])
                    else:
                        new_comments.append(comment)
                rule['comments'] = new_comments

        # Also substitute in recommendations
        recommendations = data.get('recommendations', [])
        if recommendations and isinstance(recommendations, list):
            # If it's an array of strings, substitute directly
            if recommendations and isinstance(recommendations[0], str):
                data['recommendations'] = [
                    snippets.get(rec, rec) for rec in recommendations
                ]
            # If it's an array of objects, update the 'text' field
            else:
                for rec in recommendations:
                    if 'text' in rec and rec['text'] in snippets:
                        rec['text'] = snippets[rec['text']]

        # And in requests
        requests = data.get('requests', [])
        if requests and isinstance(requests, list):
            # If it's an array of strings, substitute directly
            if requests and isinstance(requests[0], str):
                data['requests'] = [
                    snippets.get(req, req) for req in requests
                ]
            # If it's an array of objects, update the 'text' field
            else:
                for req in requests:
                    if 'text' in req and req['text'] in snippets:
                        req['text'] = snippets[req['text']]

        return data

    def build_comments_from_dcas(self, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Build comments list from DCAS rules with non-yes answers.
        Similar to the old render.py approach.

        Args:
            report: Report data dictionary

        Returns:
            List of comment dicts with 'text' and 'number' keys
        """
        comments = []
        for rule in report.get('dcas_rules', []):
            # Skip if answer is yes
            if rule.get('answer') == 'yes':
                continue
            # Skip if answer is na/yes and no comments
            if rule.get('answer') == 'yes' and not rule.get('comments'):
                continue

            # Add each comment with rule context
            if rule.get('comments'):
                for comment in rule['comments']:
                    # Normalize multi-line strings: replace newlines with spaces, strip extra whitespace
                    normalized_text = ' '.join(str(comment).split())
                    # Format: {"text": comment, "number": rule_number}
                    comments.append({
                        'text': normalized_text,
                        'number': rule.get('number')
                    })
            elif rule.get('text'):
                # If no comments but has text, use text
                normalized_text = ' '.join(str(rule['text']).split())
                comments.append({
                    'text': normalized_text,
                    'number': rule.get('number')
                })

        return comments

    def generate_report(
        self,
        report_path: str,
        template_name: str,
        snippets_path: Optional[str] = None
    ) -> str:
        """
        Generate a report by rendering template with TOML data

        Args:
            report_path: Path to report.toml file
            template_name: Name of Jinja2 template to use
            snippets_path: Optional path to snippets TOML file

        Returns:
            Rendered report text
        """
        # Load data
        report = self.load_toml_report(report_path)
        snippets = self.load_snippets(snippets_path)

        # Substitute tag references with snippet values
        report = self.substitute_tags(report, snippets)

        # Build comments from DCAS rules with non-yes answers
        report['comments'] = self.build_comments_from_dcas(report)

        # Filter empty sections
        report = self.filter_empty_sections(report)

        # Render template
        return self.render_template(template_name, report, snippets)

    def get_template_names(self) -> List[str]:
        """Get list of available Jinja2 templates"""
        return [f.name for f in self.templates_dir.glob('*.jinja2')]

    def validate_toml(self, report_path: str) -> Tuple[bool, str]:
        """
        Validate TOML report structure

        Returns:
            (is_valid, error_message)
        """
        try:
            data = self.load_toml_report(report_path)

            # Check for required sections
            if 'metadata' not in data:
                return False, "Missing required 'metadata' section"

            # Validate dcas_rules entries have required fields
            dcas_rules = data.get('dcas_rules', [])
            for i, rule in enumerate(dcas_rules):
                if 'answer' not in rule:
                    return False, f"dcas_rules[{i}] missing required 'answer' field"
                if 'text' not in rule:
                    return False, f"dcas_rules[{i}] missing required 'text' field"
                if rule['answer'] not in ['yes', 'no', 'maybe']:
                    return False, f"dcas_rules[{i}] invalid answer: {rule['answer']}"

            return True, "Valid"

        except Exception as e:
            return False, f"Error parsing TOML: {str(e)}"
