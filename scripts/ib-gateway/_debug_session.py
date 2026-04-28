"""Session interactive ib_insync avec logging DEBUG côté client.

Lancement attendu (depuis l'host PowerShell) :

    docker run --rm -it --network container:fxvol-ib-gateway \
        -v "${PWD}/scripts:/scripts" \
        python:3.11-slim sh -c "pip install -q ib_insync && python -i /scripts/ib-gateway/_debug_session.py"

Le `-it` + `python -i` te laissent en REPL Python après le setup. Tu as
alors une variable globale `ib` déjà connectée, et chaque appel imprime
en stdout les messages envoyés et reçus.

Exemples à taper dans la REPL :

    >>> ib.managedAccounts()
    >>> ib.accountSummary()
    >>> ib.positions()
    >>> ib.reqCurrentTime()
    >>> ib.disconnect()    # quand tu as fini

Pour quitter : `exit()` ou Ctrl+D.

⚠ Avant les calls, le DEBUG logger imprime ~10 lignes par seconde de
heartbeat (« sendMsg / on_message »). C'est normal. Filtre via grep
si ça pollue trop : ajoute `| grep -v heartbeat` au pipeline shell.
"""
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)-25s %(message)s",
    datefmt="%H:%M:%S",
)

# Atténue ipdb / asyncio qui sont verbeux pour rien dans ce contexte.
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("ib_insync.wrapper").setLevel(logging.INFO)  # passe à DEBUG si besoin

from ib_insync import IB  # noqa: E402

print("=" * 80)
print("ib_insync DEBUG session — REPL Python interactive")
print("=" * 80)

ib = IB()
ib.connect("127.0.0.1", 4002, clientId=190, timeout=15)
print(f"\n  connected = {ib.isConnected()}, serverVersion = v{ib.client.serverVersion()}\n")
print("Tape: ib.accountSummary(), ib.positions(), ib.managedAccounts(), ib.reqCurrentTime()")
print("Quitte: ib.disconnect() puis exit()\n")
