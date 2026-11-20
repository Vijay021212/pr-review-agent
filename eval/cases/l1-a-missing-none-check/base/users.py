USERS = {
    1: {"name": "Ada", "email": "ada@example.com"},
    2: {"name": "Grace", "email": "grace@example.com"},
}


def display_name(user_id: int) -> str:
    return USERS[user_id]["name"]
