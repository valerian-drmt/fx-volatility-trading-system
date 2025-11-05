# README — Git & GitHub with PyCharm Terminal (Quick Guide)

> Works on macOS/Linux/Windows. Commands are the same unless noted.

## 0) Prerequisites
- Install **Git**: https://git-scm.com/downloads  
- (Optional but recommended) Install **GitHub CLI**: https://cli.github.com

---

## 1) One-time Git setup on your machine
```bash
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
git config --global init.defaultBranch main
git config --global pull.rebase false
git config --global core.autocrlf input
```

### (Option A) SSH setup
```bash
ssh-keygen -t ed25519 -C "your.email@example.com"
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
ssh -T git@github.com
```

### (Option B) HTTPS
Use repo URLs starting with `https://github.com/...`.

---

## 2) Create a new Git repo (local) and connect to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:<your-username>/<repo>.git
git push -u origin main
```

---

## 3) Clone an existing GitHub repo
```bash
git clone git@github.com:<user>/<repo>.git
cd <repo>
```

---

## 4) Everyday workflow
```bash
git status
git add .
git commit -m "Short message"
git pull
git push
```

---

## 5) Branching
```bash
git checkout -b feature/my-task
git add .
git commit -m "Implement X"
git push -u origin feature/my-task
```

---

## 6) Update with main
```bash
git checkout main
git pull
git checkout feature/my-task
git merge main
```

---

## 7) Create Pull Request (PR)

### Via GitHub Website
1. Push branch.
2. Click "Compare & pull request" on GitHub.

### Via GitHub CLI
```bash
gh auth login
gh pr create --base main --head feature/my-task --title "Add X" --body "Details"
gh pr view --web
```

---

## 8) Merge PR and clean
```bash
git checkout main
git pull
git branch -d feature/my-task
git push origin --delete feature/my-task
```

---

## 9) Common fixes
```bash
git log --oneline --graph --decorate --all
git diff
git restore <file>
git stash
git stash pop
```

---

## 10) Tags & releases
```bash
git tag -a v1.0.0 -m "First release"
git push origin v1.0.0
```

---

## 11) Using PyCharm Terminal
- Open **Terminal** tab in PyCharm.
- Run Git commands directly.
- Use PyCharm Git UI for commits, branches, push/pull.
- Enable Git integration when prompted.

---

## 12) Quick Cheat Sheet
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
