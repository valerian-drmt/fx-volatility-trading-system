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
| **EC2 instance + EIP + DNS records** | ❌ pas déployé | déploiement EC2 abandonné — voir note ci-dessous |
| GHCR setup (visibilité packages) | ✅ done par défaut | repo `public` → packages publics |

> **Note déploiement EC2** : R8 prévoyait un déploiement EC2 prod ; la
> workflow `deploy.yml` et le playbook `R8_DEPLOY.md` ont été retirés.
> Les ressources support AWS (KMS, IAM, SSM, S3, DNS) restent en place
> et sont utilisées par `scripts/ops/load_secrets.{ps1,sh}` pour le
> bootstrap local des secrets. Si un déploiement EC2 reprend, repartir
> de zéro sur un nouveau plan plutôt que de reconstituer l'historique.

---

## Quoi lire selon le besoin

### Si tu veux comprendre **ce qui existe maintenant**

→ `STATE.md` — snapshot précis de toutes les ressources AWS provisionnées au 2026-04-27.

### Si tu setup un compte AWS depuis zéro

→ `SETUP.md` — plan KMS + SSM + IAM users.

### Si tu provisionnes les secrets dans SSM

→ `secrets-bootstrap.md` — procédure SSM Parameter Store + KMS encryption.

---

## Convention de mise à jour

À chaque session AWS qui modifie l'infra :

1. Mettre à jour `STATE.md` (ressources réellement existantes)
2. Mettre à jour ce `README.md` si l'organisation des fichiers change

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
| **Maintenant** | KMS + Route 53 hosted zone | **~$1.50/mo** |

Budget cap actuel : **$10/mo** avec alerte à 80%.

---

## Liens externes

- **AWS console** : https://console.aws.amazon.com (login via `itadmin` ou `fxvol-dev`, jamais root)
- **AWS Region eu-west-1** : https://eu-west-1.console.aws.amazon.com
- **Route 53** : https://us-east-1.console.aws.amazon.com/route53/v2/hostedzones (Route 53 = global mais console s'ouvre en us-east-1)
- **AWS pricing calculator** : https://calculator.aws

---

**Dernière mise à jour** : 2026-04-27 — fin de session bootstrap AWS complet.
