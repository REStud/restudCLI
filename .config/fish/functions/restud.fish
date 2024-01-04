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
    end
end
