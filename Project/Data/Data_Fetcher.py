from ib_insync import *


class DataFetcher:
    def __init__(self):
        self.raw_data = None  # Contiendra le DataFrame OHLCV

    def get_btc_data_from_ib(self, host='127.0.0.1', port=4002, client_id=1,
                             duration='5 D', bar_size='1 hour'):
        # Connexion à IB Gateway
        ib = IB()
        ib.connect(host, port, clientId=client_id)

        # Définir le contrat Crypto Spot BTC/USD via PAXOS
        btc_spot = Contract()
        btc_spot.symbol = 'BTC'
        btc_spot.secType = 'CRYPTO'
        btc_spot.exchange = 'PAXOS'
        btc_spot.currency = 'USD'

        # Vérification du contrat
        contracts = ib.reqContractDetails(btc_spot)
        if not contracts:
            ib.disconnect()
            raise Exception("No BTC spot contract found. Assure-toi que l'accès crypto est activé.")

        # Récupération des données historiques
        bars = ib.reqHistoricalData(
            btc_spot,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow='AGGTRADES',  # obligatoire pour cryptos
            useRTH=False,
            formatDate=1
        )

        # Conversion en DataFrame
        df = util.df(bars)
        self.raw_data = df[['date', 'open', 'high', 'low', 'close', 'volume']]

        # Déconnexion propre
        ib.disconnect()

        return self.raw_data

fetcher = DataFetcher()
df = fetcher.get_btc_data_from_ib()
print(fetcher.raw_data.head())
















