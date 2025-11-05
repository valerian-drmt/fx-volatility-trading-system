# 🧭 README — Git & GitHub with PyCharm Terminal (Quick Guide)

> Works on macOS/Linux/Windows. Commands are the same unless noted.

---

## ⚙️ 0) Prerequisites  
*(Install Git and configure your environment.)*
```bash
git config --global user.name "Your Name"               # Set your Git username
git config --global user.email "your.email@example.com" # Set your email
git config --global init.defaultBranch main             # Use 'main' as default branch
git config --global pull.rebase false                   # Disable automatic rebase on pull
git config --global core.autocrlf input                 # Fix line endings (macOS/Linux)
```

---

## 🔐 1) Create SSH or HTTPS connection  
*(Set up GitHub authentication for pushing and pulling securely.)*

### (Option A) SSH setup
```bash
ssh-keygen -t ed25519 -C "your.email@example.com"  # Create SSH key
eval "$(ssh-agent -s)"                             # Start SSH agent
ssh-add ~/.ssh/id_ed25519                          # Add key to agent
cat ~/.ssh/id_ed25519.pub                          # Display public key
ssh -T git@github.com                              # Test connection
```

### (Option B) HTTPS  
Use repository URLs starting with `https://github.com/...`.

---

## 🏗️ 2) Create a new Git repo (local) and connect to GitHub  
*(Initialize a repo and push your first commit.)*
```bash
git init                                           # Initialize local repo
git add .                                          # Stage all files
git commit -m "Initial commit"                     # Save initial commit
git remote add origin git@github.com:<user>/<repo>.git  # Link to GitHub repo
git push -u origin main                            # Push 'main' to GitHub
```

---

## 📥 3) Clone an existing GitHub repo  
*(Copy a remote repository to your local machine.)*
```bash
git clone git@github.com:<user>/<repo>.git         # Clone repo
cd <repo>                                          # Move into project folder
```

---

## 🔄 4) Everyday workflow  
*(Update, add changes, commit, and push updates.)*
```bash
git status                                         # Check current status
git add .                                          # Stage all changes
git commit -m "Short message"                      # Commit work
git pull                                           # Pull latest updates from main
git push                                           # Push local commits to remote
```

---

## 🌿 5) Branching  
*(Create and work on a new feature branch.)*
```bash
git checkout -b feature/my-task                    # Create and switch branch
git add .                                          # Stage changes
git commit -m "Implement feature X"                # Commit changes
git push -u origin feature/my-task                 # Push new branch to GitHub
```

---

## 🔁 6) Update with main  
*(Merge the latest changes from main into your feature branch.)*
```bash
git checkout main                                  # Switch to main
git pull                                           # Pull latest main updates
git checkout feature/my-task                       # Go back to your branch
git merge main                                     # Merge main into your branch
```

---

## 🚀 7) Create Pull Request (PR)  
*(Propose merging your branch into main.)*

### Via GitHub Website  
1. Push your branch.  
2. Click “Compare & pull request” on GitHub.  

### Via GitHub CLI  
```bash
gh auth login                                      # Authenticate GitHub CLI
gh pr create --base main --head feature/my-task   --title "Add feature X"   --body "Description of the changes"              # Create PR
gh pr view --web                                   # Open PR in browser
```

---

## 🧹 8) Merge PR and clean  
*(Merge feature branch into main and delete it.)*
```bash
git checkout main                                  # Switch to main
git pull                                           # Update main
git branch -d feature/my-task                      # Delete local branch
git push origin --delete feature/my-task           # Delete remote branch
```

---

## 🧩 9) Common fixes  
*(Inspect, revert, or temporarily store changes.)*
```bash
git log --oneline --graph --decorate --all         # Visualize commit history
git diff                                           # Show file differences
git restore <file>                                 # Revert local file changes
git stash                                          # Temporarily save work
git stash pop                                      # Restore stashed work
```

---

## 🏷️ 10) Tags & releases  
*(Tag specific commits for versioning or releases.)*
```bash
git tag -a v1.0.0 -m "First release"               # Create annotated tag
git push origin v1.0.0                             # Push tag to GitHub
```

---

## 💻 11) Using PyCharm Terminal  
*(Run Git commands directly inside PyCharm.)*
- Open the **Terminal** tab in PyCharm.  
- Run Git commands directly.  
- Use PyCharm’s Git UI for commits, branching, and pushing.  
- Enable Git integration when prompted.  

---

## ⚡ 12) Quick Cheat Sheet  
*(Essential one-liners for setup and sync.)*
```bash
git config --global user.name "Name"
git config --global user.email "you@mail.com"
git init && git add . && git commit -m "init"
git remote add origin <url>
git push -u origin main
git clone <url> && cd repo
git add . && git commit -m "msg" && git pull && git push
git checkout -b feature/x && git push -u origin feature/x
gh pr create --base main --head feature/x -t "Title" -b "Body"
```
