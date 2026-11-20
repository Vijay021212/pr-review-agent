USERS = {
    1: {"name": "Ada", "email": "ada@example.com"},
    2: {"name": "Grace", "email": "grace@example.com"},
}


def display_name(user_id: int) -> str:
    return USERS[user_id]["name"]


def email_for(user_id: int) -> str:
    """The email address of a user, by id."""
    user = USERS.get(user_id)
    return user["email"]
