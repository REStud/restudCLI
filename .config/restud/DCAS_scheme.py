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


class DCASTemplate(BaseModel):
    author: Optional[str] = None
    salutation: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
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
    
    def to_yaml(self, yaml_file_path: str) -> None:
        """Save DCAS template to a YAML file"""
        with open(yaml_file_path, "w", encoding='utf-8') as file:
            yaml.dump(self.model_dump(), file, sort_keys=False)
    
    def to_template_format(self) -> Dict[str, Any]:
        """Convert the template to a format compatible with the report generation system"""
        # Basic fields
        result = {
            "author": self.author or "",
            "salutation": self.salutation or "",
            "email": self.email or "",
            "title": self.title or "",
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


class TagsLibrary(BaseModel):
    tags: Dict[str, str]
    
    @classmethod
    def from_yaml(cls, yaml_file_path: str) -> "TagsLibrary":
        """Load tags from a YAML file"""
        with open(yaml_file_path, "r", encoding='utf-8') as file:
            data = yaml.safe_load(file)
            
            # Process the YAML anchors into a dictionary
            tags_dict = {}
            for item in data.get('tags', []):
                if isinstance(item, str) and item.startswith('&'):
                    # This is an anchor, but we don't have its value directly
                    tag_name = item[1:]  # Remove the '&'
                    tags_dict[tag_name] = "Tag value not available in raw format"
            
            return cls(tags=tags_dict)


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


def generate_report(template_path, dcas_path, constants_path=None):
    """Generate a report from template and DCAS files"""
    # Read template
    with open(template_path, 'rt', encoding='utf-8') as f:
        template_text = f.read()
    
    # Load DCAS template
    dcas_template = DCASTemplate.from_yaml(dcas_path)
    
    # Load constants if provided
    constants = {}
    if constants_path:
        with open(constants_path, 'rt', encoding='utf-8') as f:
            constants = yaml.safe_load(f)
    
    # Get content from DCASTemplate
    content = dcas_template.to_template_format()
    
    # Merge with constants
    for key, value in constants.items():
        if key not in content:
            content[key] = value
    
    # Parse content
    parsed_content = {k: parse(content[k]) for k in content}
    
    # Apply singular/plural rules
    template_text = singulars_and_plurals(template_text, content)
    
    # Format the template
    return template_text.format(**parsed_content)


def main():
    """Main function to run the script"""
    if len(sys.argv) < 3:
        print("Usage: python script.py template_file dcas_file [constants_file]")
        sys.exit(1)
    
    template_path = sys.argv[1]
    dcas_path = sys.argv[2]
    constants_path = sys.argv[3] if len(sys.argv) > 3 else None
    
    report = generate_report(template_path, dcas_path, constants_path)
    print(report)


if __name__ == "__main__":
    main()
