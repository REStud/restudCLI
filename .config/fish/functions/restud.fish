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
            restud _get_latest_version
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
            restud _check_community
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
                git checkout -b version1
            else
                echo 'there is other branch than author'
                git commit -m "update to zenodo version $argv[2]"
                git push 
                restud _get_latest_version
                git checkout -b verion$v
            end
        case report $argv[2]
            git add report.yaml
            git commit -m "update report"
            git push origin $argv[2]
        # private functions not exposed to end user
        case _get_key
            set -x ZENODO_API_KEY (head -n1 ~/.config/.zenodo_api_key)
        case _download_zenodo
            if not test -f .zenodo
                curl -Lo repo.zip "$argv[2]?access_token=$ZENODO_API_KEY"
                # for submitted records we should check cookie settings.
                echo "$argv[2]" > .zenodo
            else
                curl -Lo repo.zip (head -n1 .zenodo)"?access_token=$ZENODO_API_KEY"
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
        case _save_id
            set zenodo_id (head .zenodo | grep -o -E '/[0-9]+/' | string replace / "" -a)
            echo -e \n$zenodo_id >> .zenodo_id
        case _get_id
            set zendod_id (head .zendod_id -n 1)
        case _check_community
            restud _get_id
            if test -z (curl -i "https://zenodo.org/api/records/$zenodo_id/communities" |grep 'restud-replication')
                echo \n\n\n"Not part of REStud community."\n\n\n
                read -p "Accept into the community? (Y/N): " confirm && [[ $confirm == [yY] || $confirm == [yY][eE][sS] ]] || exit 1
                restud _community_accept
            else
                echo \n\n\n"Already part of REStud community!"\n\n\n
            end
        case _get_request
            restud _get_id
            restud _get_key
            set url "https://zenodo.org/api/communities/451be469-757a-4121-8792-af8ffc4461fb/requests?size=200&is_open=true&access_token="
            curl "$url$ZENODO_API_KEY" | jq --arg zendod_id $zenodo_id '.hits.hits[] | select(.topic.record==$zenodo_id) | .links.actions.accept' > .accept_request
        case _community_accept 
            restud _get_request
            set url (head .accept_request)
            curl -X POST -H "Content-Type: application/json" "$url?access_token=$ZENODO_API_KEY"
            rm .accept_request
        case _get_latest_version
            set v 1
            while git switch version$v
                set v (math $v + 1)
            end
    end
end
