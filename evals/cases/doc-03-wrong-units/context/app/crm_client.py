from app.http import fetch_json


def get_contact(contact_id):
    return fetch_json(f"https://crm.example/api/contacts/{contact_id}", timeout=10)


def search_contacts(query):
    return fetch_json("https://crm.example/api/contacts", params={"q": query}, timeout=60)
