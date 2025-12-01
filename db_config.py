# db_config.py
import redis
import os
import streamlit as st

def get_redis_connection():
    # Check if running on Streamlit Cloud (uses st.secrets)
    if "REDIS_URL" in st.secrets:
        return redis.from_url(st.secrets["REDIS_URL"], ssl_cert_reqs=None)
    
    # Fallback to Localhost (for local development)
    else:
        return redis.Redis(host='localhost', port=6379, db=0)
