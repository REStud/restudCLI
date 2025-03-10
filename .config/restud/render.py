import sys
import yaml

def ordered_list(list):
    output = []
    for i, item in enumerate(list):
        output.append(f'{i+1}. {item}')
    return '\n'.join(output)

def parse(value):
    if isinstance(value, list):
        return ordered_list(value)
    else:
        return value

def singulars_and_plurals(template, content):
    requests_count = 1
    recommendations_count = 1
    print(content)

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

if __name__ == "__main__":
    template = open(sys.argv[1], 'rt', encoding='utf-8').read()
    # template.yaml defines some constants that can be referred to in later .yaml 
    content = yaml.load(
        open(sys.argv[3], 'rt', encoding='utf-8').read() + '\n' +       
        open(sys.argv[2], 'rt', encoding='utf-8').read(),
        Loader=yaml.Loader
        )

    parsed_content = {k: parse(content[k]) for k in content}

    template = singulars_and_plurals(template, content)
    print(template.format(**parsed_content))    
