function restud
    switch $argv[1]
        case install
            set current_folder (pwd)
            set temp_folder (mktemp -d)
            cd $temp_folder
            git clone git@github.com:REStud/workflow.git
            cp -r workflow/.config/restud ~/.config/
            cp workflow/.config/fish/functions/restud.fish ~/.config/fish/functions/
            set -Ux RESTUD ~/.config/restud
            echo "Please add the following line to your .config/fish/config.fish file:"
            echo "set -Ux RESTUD ~/.config/restud"
            echo "Then restart your terminal."
            cd $current_folder
            rm -rf $temp_folder
        case init
            set current_folder (pwd)
            set temp_folder (mktemp -d)
            cd $temp_folder
            cp $RESTUD/pyproject.toml .
            poetry install
            poetry shell
            cd $current_folder
            set -Ux RESTUD ~/.config/restud
        case pull
            if not test -d $argv[2]
                git clone git@github.com:restud-replication-packages/$argv[2].git
                cd $argv[2]
            else
                cd $argv[2]
                git pull
            end
            set v 1
            while git switch version$v
                set v (math $v + 1)
            end
        case revise
            set branch_name (git symbolic-ref --short HEAD)
            if test "$branch_name" = "version1"
                set email_template $RESTUD/response1.txt
            else
                set email_template $RESTUD/response2.txt
            end
            python $RESTUD/render.py $email_template report.yaml $RESTUD/template.yaml > response.txt
            pbcopy < response.txt
            git add report.yaml response.txt
            git commit -m "edit report"
            git push
        case accept
            git tag accepted
            git push --tags
            set branch_name (git symbolic-ref --short HEAD)
            if test "$branch_name" = "version1"
                set email_template $RESTUD/accept1.txt
            else
                set email_template $RESTUD/accept2.txt
            end
            python $RESTUD/render.py $email_template report.yaml $RESTUD/template.yaml > accept.txt
            pbcopy < accept.txt
        case new
           mkdir $argv[2] 
           cd $argv[2]
           git init
           gh repo create restud-replication-packages/$argv[2] --private --team Replicators
           git remote add origin git@github.com:restud-replication-packages/$argv[2].git
           git checkout -b author
        case download
            git switch author
            restud _empty_folder
            restud _get_key
            restud _download_zenodo "$argv[2]"
            restud _commit
            set _branches (git branch -a | grep -v 'author')
            if test "$_branches" = ""
                echo 'there is no  other branch than author'
                git commit -m "initial commit from zenodo $argv[2]"
                git push origin author --set-upstream
                
            else
                echo 'there is other branch than author'
                git commit -m "update to zenodo version $argv[2]"
                git push 
            end
        case report
            git add report.yaml
            git commit -m "update report"
            git push
        # private functions not exposed to end user
        case _download_zenodo
            if not test -f .zenodo
                curl -Lo repo.zip "$argv[2]"
                echo "$argv[2]" > .zenodo
            else
                curl -Lo repo.zip (head -n1 .zenodo)
            end
            unzip repo.zip
            rm repo.zip
        case _commit
            find . -type f -size +20M | cut -c 3- > .gitignore
            git add .
        case _empty_folder
            set num_dirs (ll | grep ^d | wc -l)
            if test (math $num_dirs) -ne 0
                echo "Removing previous directories!" 
                ls -d */ | string replace " " "\ " > dirs
                xargs -I{} rm -rfv {} < dirs 
                rm dirs
            else 
                echo "No directories in the folder!"
            end
    end
end
