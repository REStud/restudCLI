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
