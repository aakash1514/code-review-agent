# Users API

## Create a user

`POST /v1/users`

Creates a user account and returns it. Responds with **201 Created**.

### Request body

| Field      | Type   | Required | Notes                         |
|------------|--------|----------|-------------------------------|
| `username` | string | yes      | 3-30 characters, `[a-z0-9_]`  |
| `email`    | string | yes      | must be unique                |
| `role`     | string | no       | `member` (default) or `admin` |

### Example

```http
POST /v1/users
Content-Type: application/json

{"user_name": "ada_l", "email": "ada@example.com"}
```

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"id": 42, "username": "ada_l", "email": "ada@example.com", "role": "member"}
```

### Errors

| Status | When                                   |
|--------|----------------------------------------|
| 400    | a required field is missing or invalid |
| 409    | the email is already registered        |
