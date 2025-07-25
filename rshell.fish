#!/usr/bin/env fish

# Define the function to load all custom functions
function load_all_customs
    for file in ~/.config/fish/custom_functions/*.fish
        source $file
    end
end

# Load all custom functions
load_all_customs

# Define custom prompt function
function fish_prompt
    set_color normal
    echo -n (basename (pwd))
    
    # Check if we're in a git repository
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1
        set_color yellow
        printf " (%s)" (git symbolic-ref --short HEAD 2>/dev/null; or git rev-parse --short HEAD 2>/dev/null)
    end
    
    set_color normal
    echo -n ' $ '
end

# Store the current working directory
set original_dir (pwd)

# Define the directory containing pyproject.toml (adjust as needed)
set project_dir ~/.config/restud

# Check if pyproject.toml exists in the specified directory
if test -f $project_dir/pyproject.toml
    # Change to the project directory
    cd $project_dir

    # Activate Poetry shell, change back to original directory, and launch fish
    poetry shell
    and begin
        cd $original_dir
        # Launch a new fish shell with custom prompt and Poetry environment
        fish -C "
            function fish_prompt
                set_color normal
                echo -n "[RESTUD]" (basename (pwd))
                if git rev-parse --is-inside-work-tree >/dev/null 2>&1
                    set_color yellow
                    printf ' (%s)' (git symbolic-ref --short HEAD 2>/dev/null; or git rev-parse --short HEAD 2>/dev/null)
                end
                set_color normal
                echo -n '> '
            end
            set -g POETRY_ACTIVE 1
            echo 'Poetry environment activated. Working directory: '(pwd)
        "
    end
else
    echo "No pyproject.toml found in $project_dir. Are you sure this is the correct directory?"
    echo "Launching a regular fish shell instead."
    fish -C "
            function fish_prompt
                set_color normal
                echo -n "[RESTUD]" (basename (pwd))
                if git rev-parse --is-inside-work-tree >/dev/null 2>&1
                    set_color yellow
                    printf ' (%s)' (git symbolic-ref --short HEAD 2>/dev/null; or git rev-parse --short HEAD 2>/dev/null)
                end
                set_color normal
                echo -n '> '
            end
    "
end