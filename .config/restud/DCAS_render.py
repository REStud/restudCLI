from enum import Enum
from typing import Dict, List, Optional, Union, Any
import sys
import yaml
from pydantic import BaseModel, Field, field_validator


class ResponseType(str, Enum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"
    NOT_APPLICABLE = "not_applicable"


class DCASRuleItem(BaseModel):
    section: str
    item: str
    answer: Optional[ResponseType] = None
    description: str
    order: int
    exemption_reason: Optional[str] = None


class ReportTemplate(BaseModel):
    author: str
    salutation: str
    email: str
    title: str
    praise: Optional[str] = None
    requests: Optional[Union[List[str], str]] = []
    recommendations: Optional[Union[List[str], str]] = []
    DCAS_rules: List[DCASRuleItem]
    tags: Optional[List[str]] = []
    
    @field_validator('requests', 'recommendations', mode='before')
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

    @classmethod
    def from_yaml(cls, yaml_file_path: str) -> "DCASTemplate":
        """Load DCAS template from a YAML file"""
        with open(yaml_file_path, "r", encoding='utf-8') as file:
            data = yaml.safe_load(file)
        return cls.model_validate(data)
    
    def to_template_format(self) -> Dict[str, Any]:
        """Convert the template to a format compatible with the report generation system"""
        # Basic fields
        result = {
            "author": self.author,
            "salutation": self.salutation,
            "email": self.email,
            "title": self.title,
            "praise": self.praise or "",
            "requests": self.requests or [],
            "recommendations": self.recommendations or [],
        }
        
        # Format DCAS rules with answers for the template
        dcas_items = []
        for rule in sorted(self.DCAS_rules, key=lambda x: x.order):
            answer_text = rule.answer.value if rule.answer else "not_evaluated"
            formatted_item = f"{rule.section} - {rule.item}: {answer_text}"
            
            if rule.exemption_reason:
                formatted_item += f" (Exempt: {rule.exemption_reason})"
                
            dcas_items.append(formatted_item)
        
        result["dcas_items"] = dcas_items
        
        # Add tags if present
        if self.tags:
            result["tags"] = self.tags
            
        return result

def ordered_list(list_items):
    """Format a list as an ordered list with numbers"""
    if not list_items:
        return ""
        
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


def generate_report(template_path, report_path, tags_path, dcas_path):
    """Generate a report from template and DCAS files"""
    # Read template
    with open(template_path, 'rt', encoding='utf-8') as f:
        template_text = f.read()
    
    content = yaml.load(
        open(tags_path, 'rt', encoding='utf-8').read() + '\n' +       
        open(report_path, 'rt', encoding='utf-8').read() + '\n' +
        open(dcas_path, 'rt', encoding='utf-8').read(),
        Loader=yaml.Loader
    )
    # Load DCAS template
    report = ReportTemplate.from_yaml(content)    
    # Get content from DCASTemplate
    content = report.to_template_format()    
    # Parse content
    parsed_content = {k: parse(content[k]) for k in content}
    
    # Apply singular/plural rules
    template_text = singulars_and_plurals(template_text, content)
    
    # Format the template
    return template_text.format(**parsed_content)


def main():
    """Main function to run the script"""
    if len(sys.argv) < 5:
        print("Usage: python script.py template_file dcas_file [constants_file]")
        sys.exit(1)
    
    template_path = sys.argv[1]
    report_path = sys.argv[2]
    dcas_path = sys.argv[3]
    answers_path = sys.argv[4] 

    report = generate_report(template_path, dcas_path, answers_path, dcas_path)
    print(report)


if __name__ == "__main__":
    main()
