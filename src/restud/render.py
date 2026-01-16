from enum import Enum
from typing import Dict, List, Optional, Union, Any
import sys
import yaml
from yamlcore import CoreLoader
from pydantic import BaseModel, Field, field_validator


class ResponseType(str, Enum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"
    NA = "na"


class DCASRuleItemV2(BaseModel):
    description: str
    answer: ResponseType
    comment: Optional[Union[List[str], str]] = None
    dcas_reference: str

class DCASRuleItemV3(BaseModel):
    name: str
    description: str
    answer: ResponseType
    comment: Optional[Union[List[str], str]] = None
    dcas_reference: str

class ReportTemplate(BaseModel):
    version: int
    author: str
    salutation: str
    email: str
    title: str
    manuscript_id: Optional[str] = None
    praise: Optional[str] = None
    DCAS_rules: List[Union[DCASRuleItemV2, DCASRuleItemV3]]
    recommendations: Optional[Union[List[str], str]] = []
    tags: List[str]

    @field_validator('DCAS_rules', 'recommendations', mode='before')
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

    @classmethod
    def from_dict(cls, yaml_dict: dict) -> "DCASTemplate":
        """Load DCAS template from a dict"""
        return cls.model_validate(yaml_dict)

    def to_template_format(self) -> Dict[str, Any]:
        """Convert the template to a format compatible with the report generation system"""
        # Basic fields
        result = {
            "version": self.version,
            "author": self.author,
            "salutation": self.salutation,
            "email": self.email,
            "title": self.title,
            "manuscript_id": self.manuscript_id or "",
            "praise": self.praise or "",
            "requests": "",
            "recommendations": self.recommendations or [],
            "DCAS_rules": self.DCAS_rules,
            "tags": self.tags
        }        # Format DCAS rules with answers for the template
        requests = []
        for rule in self.DCAS_rules:
            if rule.answer.value == "yes":
                continue
            if rule.answer.value == "na" and rule.comment is None:
                continue

            # Debug: write to file
            with open('/tmp/debug_render.log', 'a') as f:
                f.write(f"DEBUG: rule.name={rule.name}, answer={rule.answer.value}, comment={rule.comment}, type={type(rule.comment)}\n")

            if isinstance(rule.comment,List):
                for comment in rule.comment:
                    formatted_item = f"{comment} ({rule.dcas_reference})"
                    requests.append(formatted_item)
            else:
                if rule.comment:  # Only add if comment is not empty
                    formatted_item = f"{rule.comment} ({rule.dcas_reference})"
                    requests.append(formatted_item)

        result["requests"] = requests
        return result

def ordered_list(list_items):
    """Format a list as an ordered list with numbers"""
    if not list_items:
        return ""

    # If there's only one item, return it without numbering
    if len(list_items) == 1:
        return list_items[0]

    output = []
    for i, item in enumerate(list_items):
        output.append(f'{i+1}. {item}')
    return '\n'.join(output)


def parse(value):
    """Parse different types of values for template rendering"""
    if isinstance(value, list):
        return ordered_list(value)
    else:
        return value


def singulars_and_plurals(template, content):
    """Adjust template text for singular/plural forms based on content"""
    requests_count = 1
    recommendations_count = 1

    # Check if the items are lists and count them
    if isinstance(content.get('requests', []), list):
        requests_count = len(content['requests'])

    if isinstance(content.get('recommendations', []), list):
        recommendations_count = len(content['recommendations'])

    # Replace the template text with appropriate singular/plural forms
    if requests_count == 1:
        template = template.replace("please make the following changes:",
                                    "please make the following change:")

    if recommendations_count == 1:
        template = template.replace("please consider the following recommendations",
                                   "please consider the following recommendation")

    return template


def generate_report(template_path, report_path, tags_path):
    """Generate a report from template and DCAS files"""
    # Read template
    with open(template_path, 'rt', encoding='utf-8') as f:
        template_text = f.read()

    content = yaml.load(
        open(tags_path, 'rt', encoding='utf-8').read() + '\n' +
        open(report_path, 'rt', encoding='utf-8').read(),
        Loader=CoreLoader
    )

    if content.get("version") >= 2:
        # Load into DCAS template
        report = ReportTemplate.from_dict(content)
        # Get content from DCASTemplate
        content = report.to_template_format()
        # Parse content
        parsed_content = {k: parse(content[k]) for k in content}
        # Apply singular/plural rules
        template_text = singulars_and_plurals(template_text, content)
        # Remove recommendations section if empty
        if not content.get('recommendations') or len(content.get('recommendations', [])) == 0:
            template_text = template_text.replace(
                "In addition, please consider the following recommendations to ease reproducibility:\n\n{recommendations}\n\n",
                ""
            )
        # Format the template
    else:
        parsed_content = {k: parse(content[k]) for k in content}
        template_text = singulars_and_plurals(template_text, content)

    return template_text.format(**parsed_content)


def main():
    """Main function to run the script"""
    if len(sys.argv) < 3:
        print("Usage: python script.py template_response_file report_file template_answers_file")
        sys.exit(1)

    template_path = sys.argv[1]
    report_path = sys.argv[2]
    tags_path = sys.argv[3]

    report = generate_report(template_path, report_path, tags_path)
    print(report)


if __name__ == "__main__":
    main()