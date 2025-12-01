import redis, pickle
r = redis.Redis(host='localhost', port=6379, db=0)
r.set("portfolio:cash", 1000000.0)
initial_holdings = {t: {'qty': 0, 'avg_price': 0.0} for t in ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]}
r.set("portfolio:holdings", pickle.dumps(initial_holdings))
print("Reset Complete.")