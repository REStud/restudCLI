function restud
    switch $argv[1]
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
            git add report.yaml response.txt
            git commit -m "edit report"
            git push
        case accept
            git tag accepted
            git push --tags
        case download
            if not test -f .zenodo
                curl -Lo repo.zip "$argv[2]"
                echo "$argv[2]" > .zenodo
            else
                xargs .zenodo | curl -Lo repo.zip 
            end
        case new
           mkdir $argv[2] 
           cd $argv[2]
           git init
           gh repo create restud-replication-packages/$argv[2] --private --team Replicators
           git remote add origin git@github.com:restud-replication-packages/$argv[2].git
           git checkout -b author
        case zenodo-pull
            if count * > 0
                git switch author
                ls -d */ | xargs rm -rf
            end
            restud download $argv[2]
            unzip repo.zip
            rm repo.zip
            find . -type f -size +20M | cut -c 3- > .gitignore
            git add .
            if test -n (git branch | grep -v 'author')
                echo 'there is other branch than author'
                git commit -m "update to zenodo $argv[2]"
            else
                echo 'there is no other branch than author'
                git commit -m "initial commit"
            end
            git push origin author
    end
end
