source ~/.moltrust_secrets && python3 -c "
import tweepy, os
client = tweepy.Client(
    consumer_key=os.environ['X_CONSUMER_KEY'],
    consumer_secret=os.environ['X_CONSUMER_SECRET'],
    access_token=os.environ['X_ACCESS_TOKEN'],
    access_token_secret=os.environ['X_ACCESS_SECRET']
)
tweet = 'ERC-8004 has 21,500+ agents on-chain. But agents live across chains and platforms. MolTrust bridges on-chain and off-chain trust with W3C DIDs + Ed25519 VCs + Lightning. Complementary, not competing. #ERC8004 #AgentEconomy'
r = client.create_tweet(text=tweet)
print('Posted! ID:', r.data['id'])
"
