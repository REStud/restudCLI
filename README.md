# Onboading for REStud replication team
The most important tools we use during the replication process are:
- Github
- Trello board
- Zenodo archives
- Slack and emails
  
## Github
![github](https://github.com/REStud/workflow/assets/47605029/407ec316-e184-4fd8-93dd-c6387ff88212)

We create a repository for each package named after it's manuscript number and document all the changes in the codes, exhibits or data and create a report based on all the changes needed and missing aspects of the replication package. Any person onbaording at the REStud replication team should ask *Miklós* for permissions to be a member of the [organization](https://github.com/restud-replication-packages).

## Trello board
![trello_board](https://github.com/REStud/workflow/assets/47605029/bcfc9e7f-58b4-4784-bb2e-c6b45badfa4c)

This is the team kanban board, we use it to track the workflow of the packages and communicate some specifics about them. These are usually comments about where to reach the packages if the zenodo link is not working or any other necesasry or peculiar information on the replication packges. Your trello account can be linked to any of the usual account (google, apple, microsoft etc.) You should create a trello account and ask *Miklós* for invitation or be invited through your choice of account. After having the permissions to use the board you will be able to reach it [here](https://trello.com/b/7YNdeENt/restud-replications)

## Zenodo archives
![image](https://github.com/REStud/workflow/assets/47605029/bbbf3155-948d-4ae6-ad30-c7e728801f4d)

The Zenodo archives are the public sharing and archiving portal for the authors to share their replication packages with us. You can reach the zenodo community [here](https://zenodo.org/communities/restud-replication/?page=1&size=20), the community stores all the submitted packages. There is no need for any permissions or accounts as the packages stored on zenodo are all public.

## Slack
You should be invited to the CEU Microdata slack organization's restud-packages chanell. You can be invited by any channel member.

## Emails
There is also a microsoft exchange outlook account under dataeditor@restud.com, which is Miklós' restud email account, the credentials might or might not be shared with the onboarding person depending on the role they will fill in for the replication team.

If there are any questions during the onboarding process please reach out to us by email or slack, preferably slack if you already have it set up.

# Replication workflow for REStud packages

The order of work with packages is the following:
	1. orange packages = short reviews, in order of inflow (descending order in the columns)
 	2. red packages = urgent reviews, in order of inflow
	3. purple packages = revisions, second or higher rounds, in order of inflow
	4. all the others in order of inflow

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
	- If there is no link on the card, check the submission form comment (usually the first comment on any card), and check if the paper is subject to the [__Data Availability Policy__](https://restud.github.io/data-editor/before/#data-availability-policy)
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


## In case of started or second round packages:
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
