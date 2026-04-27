# `infrastructure/aws/` — index

> Documentation de l'infrastructure AWS pour le projet fxvol.
> **À lire en premier** par Claude Code et tout contributeur humain.

---

## État global

| Élément | Statut | Référence |
|---|---|---|
| Hardening sécurité (KMS, IAM, MFA) | ✅ done | `STATE.md` § 1 |
| Storage backups (S3) | ✅ done | `STATE.md` § 2 |
| Network (Security group, SSH key) | ✅ done | `STATE.md` § 3 |
| Observability (CloudWatch, SNS) | ✅ done | `STATE.md` § 4 |
| Cost protection (Budget, Anomaly) | ✅ done | `STATE.md` § 5 |
| DNS (domaine, hosted zone, délégation) | ✅ done | `STATE.md` § 6 |
| **EC2 instance + EIP + DNS records** | 🔒 différé R8 | `R8_DEPLOY.md` |
| GHCR setup (visibilité packages) | 🔒 différé R6 | `DEPLOYMENT_PREP.md` § 5 |

---

## Quoi lire selon le besoin

### Si tu veux comprendre **ce qui existe maintenant**

→ `STATE.md` — snapshot précis de toutes les ressources AWS provisionnées au 27/04/2026.

### Si tu fais le déploiement **R8 (~12/05/2026)**

→ `R8_DEPLOY.md` — séquence exacte des commandes à lancer le jour J. Toutes les ressources support (KMS, IAM role, Security group, S3, DNS) sont déjà créées.

### Si tu veux comprendre la **trajectoire pré-session**

→ Les 3 fichiers ci-dessous (au top-level du dossier) contiennent les plans d'origine, conservés pour traçabilité :
- `SETUP.md` — plan KMS+SSM+IAM users (rédigé avant que la session bootstrap soit faite)
- `DEPLOYMENT_PREP.md` — plan phases A-H pour R8 (rédigé avant la session)
- `secrets-bootstrap.md` — détail technique secrets (commit pré-existant `b23865f5` + `3e3bd556`)

Ces 3 fichiers sont **historiques**. Ne pas s'y référer pour l'état courant : utiliser `STATE.md`. Ils seront éventuellement déplacés dans un sous-dossier `_archive/` lors d'une PR R9 dédiée à la couche AWS ops.

---

## Convention de mise à jour

À chaque session AWS qui modifie l'infra :

1. Mettre à jour `STATE.md` (ressources réellement existantes)
2. Si nouvelle phase de déploiement : créer un nouveau fichier `R<N>_DEPLOY.md` ou amender l'existant
3. Mettre à jour ce `README.md` si l'organisation des fichiers change

---

## Compte AWS

```
Account ID  : 552269855056
Region cible : eu-west-1 (Ireland)
Tag projet  : Project=fxvol (sur toutes les ressources)
```

---

## Ressources critiques (identifiants à connaître)

```
KMS CMK           alias/fxvol-secrets, KeyId bbc7ef4a-0b3e-4019-a7db-4502c4662f30
S3 backup bucket  fxvol-backups
Security group    sg-0c96af5e3203ffeec
SSH keypair       fxvol-ec2-key (ID key-0ce890402b6ab6a47)
IAM role          fxvol-ec2-secrets-role + instance profile fxvol-ec2-instance-profile
SNS alert topic   fxvol-alarms
Domain            valeriandarmente.dev (Route 53 hosted zone, délégué depuis GoDaddy)
```

---

## Coût mensuel courant

| Période | Ressources actives | Coût |
|---|---|---|
| **Maintenant (post-bootstrap)** | KMS + Route 53 hosted zone | **~$1.50/mo** |
| Après R8 (~12/05) | + EC2 t3.small + EBS + EIP attachée + S3 backups | **~$22/mo** |

Budget cap actuel : **$10/mo** avec alerte à 80%. À monter à $25/mo le jour de R8.

---

## Liens externes

- **AWS console** : https://console.aws.amazon.com (login via `itadmin` ou `fxvol-dev`, jamais root)
- **AWS Region eu-west-1** : https://eu-west-1.console.aws.amazon.com
- **Route 53** : https://us-east-1.console.aws.amazon.com/route53/v2/hostedzones (Route 53 = global mais console s'ouvre en us-east-1)
- **AWS pricing calculator** : https://calculator.aws

---

**Dernière mise à jour** : 2026-04-27 — fin de session bootstrap AWS complet.
