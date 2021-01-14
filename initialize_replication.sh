number=$1  #first argument to call is the MS number
zenodo_link=$2 # second argument to call is the zenodo link

#run from any directory where you want to work on replications, originally I use a replications parent folder where I collect the folders of the replications.
mkdir $number
cd $number
git init
git lfs install
git remote add origin git@github.com:restud-replication-packages/$number.git
wget -O replication.zip "$zenodo_link"
unzip replication.zip
rm replication.zip
subl report.yaml
