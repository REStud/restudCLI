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
            if test (string match -r "preview" "$argv[2]")
                restud _download_zenodo_preview "$argv[2]"
            else
                restud _download_zenodo "$argv[2]"
            end
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
                set -gx v (math $v + 1)
                git checkout -b version$v
            end
            restud _save_id
        case report $argv[2]
            git add report.yaml
            git commit -m "update report"
            git push origin $argv[2]
        # private functions not exposed to end user
        case _get_key
            set -gx ZENODO_API_KEY (head -n 1 ~/.config/.zenodo_api_key)
        case _download_zenodo
            curl -Lo repo.zip "$argv[2]?access_token=$ZENODO_API_KEY"
            echo "$argv[2]" > .zenodo
            unzip repo.zip
            rm repo.zip
        case _download_zenodo_preview
            restud _get_cookie
            curl -b "session=$cookie_value" -Lo repo.zip "$argv[2]"
            echo "$argv[2]" > .zenodo
            unzip repo.zip
            rm repo.zip
        case _get_cookie
            if not test -f ~/.config/restud-cookie.json
                restud _create_cookie
            end
            set -x expr_dt (jq .exp_date ~/.config/restud-cookie.json | string replace \" "" -a)
            set -x today (date +%Y-%m-%d) 
            if not test (date -d $expr_dt +%s) -gt (date -d $today +%s)
                restud _create_cookie
            end
            set -gx cookie_value (jq .value ~/.config/restud-cookie.json | string replace \" "" -a)
        case _create_cookie
            echo \n\n\n"Your restud cookie either does not exist or expired, to download preview records you need to create a new one!"\n\n\n
            read -P "Do you want to create a new one into ~/.config/restud-cookie.json? (y/n)" -n 1 -x confirm
            if test $confirm != "y"
                    return 1
            end
            echo "To create a new cookie you need two values. The zenodo session cookie value and expiration date. You can access them by opening zenodo.org, logging in and then access the developer tools panel. You can open it by F12/ctrl+shift+i or through settings/more tools/developer tools. Then look for Application sheet in the developer tools, in that sheet look for Storage/Cookies. It should list all active cookies for the page, there you need the cookie named session."
            read -P "Please copy cookie value: " -x value
            read -P "Please copy expiration date: " -x expr_dt
            jq -n --arg value $value --arg date $expr_dt '{"name":"session", "value":$value, "exp_date":$date}' > ~/.config/restud-cookie.json
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
        case _get_id
            set -gx ZENODO_ID (head .zenodo | grep -o -E '/[0-9]+/' | string replace / "" -a)
        case _check_community
            restud _get_id
            if test -z (curl -i "https://zenodo.org/api/records/$ZENODO_ID/communities" | grep 'restud-replication')
                echo \n\n\n\t"Replication package of $ZENODO_ID is not part of REStud community."\n\n\n
                read -P "Accept into the community? (y/n)" -n 1 -x confirm
                if test $confirm != "y"
                    return 1
                end
                restud _community_accept
            else
                echo \n\n\n"Already part of REStud community!"\n\n\n
            end
        case _get_accept_request
            restud _get_id
            restud _get_key
            set url "https://zenodo.org/api/records/$ZENODO_ID/requests"
            curl "$url?access_token=$ZENODO_API_KEY" | jq --arg zendod_id $ZENODO_ID '.hits.hits[].links.actions.accept' > .accept_request
        case _community_accept 
            restud _get_accept_request
            set url (head .accept_request | string replace \" "" -a)
            curl -X POST "$url?access_token=$ZENODO_API_KEY"
            rm .accept_request
        case _get_latest_version
            set -gx v (git branch -r | grep 'version' | grep -o -E '[0-9]+' | tail -1 )
    end
end
