# Replication workflow for REStud packages

The order of work with packages is the following:
	1. orange packages = short reviews, in order of inflow (descending order in the columns)
	2. purple packages = revisions, second rounds, in order of inflow
	3. all the others in order of inflow

1. Open the [REStud replications trello board](https://trello.com/b/7YNdeENt/restud-replications)

## Initialize new package

2. Open a card in the *Author submitted* column and go in descending order.
3. move the card to the lowest position of the *At team* column
4. Create a new git repo on the [REStud replication packages organization](https://github.com/restud-replication-packages)
	- the repo should be created empty as frequently the packages have a README.md
5. Make a new directory on your machine/server wherever you work.
	- It is not a necessity but I like to name the folders by MS numbers just like the git repos.
6. Initializie git on the new folder and add the remote you created before.
7. Open the zenodo link on the card in the comment section, it should look like __*https://zenodo.org/record/somenumbers*__
	- If there is no link on the card, check the submission form comment (usually the first comment on any card), and check if the paper is subject to the __Data Availability Policy__
		- If not, then our job is done as we do not have to check the package.
		- If yes, then ask Miklós about the zenodo link, this part is mostly automatized on his part, but there are some mistakes sometimes.

For the steps 8-10 I use the ***initialize_replication.sh***.

8. Download the replication zip package from zenodo.
9. Unzip the package into the folder
10. Commit the unpacked package
	- If the zip was downloaded in the folder, remove it.
	- If there are large (>20 MB) files, initialize git lfs.
		- git lfs track the large files
		- some help in how to initialize and use git lfs in the folder is in ***init_ex_for_lfs.sh***, it is not a working code rather just some frame
	- git add all the files
	- git commit with commit message "add files from *zenodo link*"
11. Create report.yaml with the structre shown in the example.


## In case of started or second round packges:
### First time we see it
12. Open the corresponding trello card
13. Run through the checklist on the trello card
	- if the codes consume too much time to run, it is better to rather just roughly check through the codes
	- Most frequent mistakes:
		- relative path
		- '\' instead of '/' (previous only works in windows environment)
		- not saved outputs
		- missing guides to install packages/toolboxes that are needed for the code
		- data citation and official DAS (Data Availabilty Statement) 
14. Write the observed mistakes into the request/recommendation part
	- for that we have a template about what are the standard codes in the *report.yaml* included in template_answers.txt
	- it is possible that you observe something that is working the way it is submitted, but you have some quality of life improvement advices or better practices, etc. These go into the recommendation part.
15. Commit the report.yaml, usually I use this as a message: *add report.yaml*
16. If finished with the trello check list move the card to *At editor*
	- it is not necessary to have everything checked the first time as the packages should iterate between us and the authors until the package is acceptable.

### Updating packages:

This part is important because in most cases we have to check a package at least twice.

17. git pull the package
18. open the zenodo link and download the updated zip
	- **Take care here which version you download as you have to manually switch the version!**
19. Repeat steps 9-10
20. Repeat steps 13-16
	- Mainly here we check whether the updated package corrected what we asked for in the requests.
