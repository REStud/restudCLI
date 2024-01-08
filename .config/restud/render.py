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

if __name__ == "__main__":
    template = open(sys.argv[1], 'rt', encoding='utf-8').read()
    # template.yaml defines some constants that can be referred to in later .yaml 
    content = yaml.load(
        open(sys.argv[3], 'rt', encoding='utf-8').read() + '\n' +       
        open(sys.argv[2], 'rt', encoding='utf-8').read(),
        Loader=yaml.Loader
        )

    parsed_content = {k: parse(content[k]) for k in content}

    print(template.format(**parsed_content))

    