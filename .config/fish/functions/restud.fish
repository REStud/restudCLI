function restud
    switch $argv[1]
        case install
            set temp_folder (mktemp -d)
            cd $temp_folder
            git clone git@github.com:REStud/workflow.git
            cp -r workflow/.config/restud ~/.config/
            cp workflow/.config/fish/functions/restud.fish ~/.config/fish/functions/
            set -Ux RESTUD ~/.config/restud
            echo "Please add the following line to your .config/fish/config.fish file:"
            echo "set -Ux RESTUD ~/.config/restud"
            echo "Then restart your terminal."
            cd -
            rm -rf $temp_folder
        case init
            echo "Not implemented yet. Did you mean 'restud install'?"
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
                python3 $RESTUD/render.py $RESTUD/response1.txt report.yaml $RESTUD/template.yaml > response.txt
            else
                python3 $RESTUD/render.py $RESTUD/response2.txt report.yaml $RESTUD/template.yaml > response.txt
            end
            pbcopy < response.txt
            git add report.yaml response.txt
            git commit -m "edit report"
            git push
        case accept
            git tag accepted
            git push --tags
            set branch_name (git symbolic-ref --short HEAD)
            if test "$branch_name" = "version1"
                python3 $RESTUD/render.py $RESTUD/accept1.txt report.yaml $RESTUD/template.yaml > accept.txt
            else
                python3 $RESTUD/render.py $RESTUD/accept2.txt report.yaml $RESTUD/template.yaml > accept.txt
            end
            pbcopy < accept.txt
    end
end
