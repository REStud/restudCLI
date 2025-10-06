# REStud CLI Tool

A Python command-line tool for managing REStud workflow operations, replacing the previous fish function and separate Python script. All template files are included in the package installation - no separate configuration needed.

## Installation

Install globally with uv:

```bash
uv tool install git+ssh://git@github.com/REStud/workflow.git
```

Or clone the repository and use `uv run` for development:

```bash
git clone git@github.com:REStud/workflow.git
cd workflow
uv run restud --help
```

For development, `uv run` handles dependencies automatically.

## Migration from Fish Function

The Python CLI provides identical functionality to the original fish function. All commands work the same way:

```bash
restud pull mypackage    # Same as before
restud revise           # Same as before  
restud accept           # Now includes Zenodo community acceptance
```

### Interactive Shell

Use the `restud shell` command to enter a rich interactive shell:

```bash
restud shell
```

Features:
- **Smart prompt** showing current folder and git branch
- **Report status indicators**:
  - `report` (green) - no DCAS rules have "no" answers
  - `report` (red) - at least one DCAS rule has "no" answer
  - `accepted` (bold green) - if git tag "accepted" exists
- **Built-in cd command** that persists directory changes
- **Arrow key support** with command history and editing
- **Tab completion** for file and directory names
- **REStud commands** work directly: `pull mypackage`, `revise`, `accept`, etc.
- **Shell passthrough** for non-REStud commands
- **Built-in help** with `help` command
- **Colored output** for better readability
- Type `exit` to quit

## Usage
Create a repository for a new replication package 29123:
```bash
restud new 29123
```
This creates a new repo on Github and clones it to your local machine. The default branch is called `author`.

Download files from Zenodo URL https://zenodo.org/record/12345:
```bash
restud download https://zenodo.org/record/12345
```
Automatically switches to `author` branch if not already there. Deletes all the files in the current directory (except `.git`). Downloads and unzips the files to the current directory. The large files are gitignored, the small ones are committed and pushed to `author` branch.

Commit the report:
```bash
restud report ?version?
```

Pull the most recent version of the package from GitHub:
```bash
restud pull 29123
```
This clones or pulls (if exists) the package 29123 and iterates through versions until the last one is checked out.

Render the report for a revision:
```bash
restud revise
```
This renders `response.txt` from `report.yaml` and copies it to the clipboard so that the data editor can paste it in the email to the author. Both files are committed and pushed to the respective `version` branch. This action stops the clock on the revision for the editorial team.

Render the acceptance email and accept into Zenodo community:
```bash
restud accept
```
This renders `accept.txt` from `report.yaml` and copies it to the clipboard so that the data editor can paste it in the email to the author. Additionally, it automatically accepts the package into the REStud Zenodo community. Both files are committed and pushed to the respective `version` branch. The commit is tagged `accepted` and the tag is pushed to GitHub. This action stops the clock for the editorial team.

# Onboading for REStud replication team
The most important tools we use during the replication process are:
- Github
- Trello board
- Zenodo archives
- tresorit/Data Editor tresor
- Slack and emails
- Watch replication videos
  
## Github
![github](https://github.com/REStud/workflow/assets/47605029/407ec316-e184-4fd8-93dd-c6387ff88212)

We create a repository for each package named after it's manuscript number and document all the changes in the codes, exhibits or data and create a report based on all the changes needed and missing aspects of the replication package. Any person onbaording at the REStud replication team should ask *@korenmiklos* for permissions to be a member of the [organization](https://github.com/restud-replication-packages).

## Trello board
![trello_board](https://github.com/REStud/workflow/assets/47605029/bcfc9e7f-58b4-4784-bb2e-c6b45badfa4c)

This is the team kanban board, we use it to track the workflow of the packages and communicate some specifics about them. These are usually comments about where to reach the packages if the zenodo link is not working or any other necesasry or peculiar information on the replication packges. Your trello account can be linked to any of the usual account (google, apple, microsoft etc.) You should create a trello account and ask *@korenmiklos* for invitation or be invited through your choice of account. After having the permissions to use the board you will be able to reach it [here](https://trello.com/b/7YNdeENt/restud-replications)

## Zenodo archives
![image](https://github.com/REStud/workflow/assets/47605029/bbbf3155-948d-4ae6-ad30-c7e728801f4d)

The Zenodo archives are the public sharing and archiving portal for the authors to share their replication packages with us. You can reach the zenodo community [here](https://zenodo.org/communities/restud-replication/?page=1&size=20), the community stores all the submitted packages. There is no need for any permissions or accounts as the packages stored on zenodo are all public.

Useful link for authors on [record submission](https://help.zenodo.org/docs/share/submit-to-community).

## Tresorit
![image](https://github.com/REStud/workflow/assets/47605029/7eb60f97-43fa-4ca3-b0b4-ac85f0a8d5fe)

There are cases when the zendod download is not working properly because the package is too large or the authors want to share proprietary/confidential data with us for replication revision purposes. In this case we use Tresorit, we send them a link to upload their replication package to the incoming folder of our Data Editor tresor. It might also happend that the authors share their pacakge on dropbox and we are the ones uploading them to tresorit to share it with other replicators in the team. You should register a tresorit account (process is similar to trello) and then ask *@korenmiklos* for permissions to reach it.

## Slack
You should be invited to the CEU Microdata slack organization's restud-packages chanell. You can be invited by any channel member.

## Emails
There is also a microsoft exchange outlook account under dataeditor@restud.com, which is Miklós' restud email account, the credentials might or might not be shared with the onboarding person depending on the role they will fill in for the replication team.

## Replication videos
There are 2 versions of the videos for replication: the first is created by @korenmiklos in 2020 as the original explanatory instructions about the replication process, the second is created by @gergelyattilakiss in 2022 as an update and documentation on the same process. These are taking ~30 mins for Miklos' version and ~3*7 mins for Gergely's version and can be reached in the Data Editor tresor.


If there are any questions during the onboarding process please reach out to us by email or slack, preferably slack if you already have it set up.

# Replication workflow for REStud packages

All steps considering downloads and 
The order of work with packages is the following:
	1. orange packages = short reviews, in order of inflow (descending order in the columns)
 	2. red packages = urgent reviews, in order of inflow
	3. purple packages = revisions, second or higher rounds, in order of inflow
	4. all the others in order of inflow

1. Open the [REStud replications trello board](https://trello.com/b/7YNdeENt/restud-replications)

## Initialize new package

2. Open a card in the *Author submitted* column and go in descending order.
3. move the card to the lowest position of the *At team* column
4. Create a new git repo, new repo on machine and initialize git with the proper remote. This is all done with `restud new`.
5. Open the zenodo link on the card in the comment section, it should look like *https://zenodo.org/record/somenumbers*.
6. Download the replication zip package from zenodo. Unzip the package into the folder. Commit the unpacked package. These are done using `restud download`. The exact steps are:
	- Download and unzip the zenodo record into the current folder.
 	- If there are large (>20 MB) files, ignore them.
	- git add and commit all the files
7. Create next `version` branch and `report.yaml`.
8. Open the corresponding trello card
9. Run through the checklist on the trello card
	- if the codes consume too much time to run, it is better to rather just roughly check through the codes
	- Most frequent mistakes:
		- relative path
		- '\' instead of '/' (previous only works in windows environment)
		- not saved outputs
		- missing guides to install packages/toolboxes that are needed for the code
		- data citation and official DAS (Data Availabilty Statement)
10. When you checked everything within the package use `restud report` for commiting and pushing the report and version branch to github.
11. Move the card to *At editor*
	- it is not necessary to have everything checked the first time as the packages should iterate between us and the authors until the package is acceptable.

### Updating packages:

This part is important because in most cases we have to check a package at least twice.

12. git pull the package
13. open the zenodo link and use `restud download`.
	- **Take care here which version you download as you have to manually switch the version sometimes!**
14. Repeat steps 8-11
	- Mainly here we check whether the updated package corrected what we asked for in the requests.
