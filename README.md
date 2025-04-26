# Git Workflow for Trading_Projects

## Initial Setup (only once)

git init
git --version
git remote add origin https://github.com/valerian-drmt/Trading_Projects.git
git config --global user.email "valeriandarmente@gmail.com"
git config --global user.name "valerian-drmt"
git add .
git commit -m "First commit"
git branch -M main
git push -u origin main

## Daily Workflow:

- When starting work in VS Code:

git pull origin main

- After finishing work in VS Code:

git add .
git commit -m "Code update"
git push origin main
