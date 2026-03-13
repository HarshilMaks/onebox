from jose import jwt, JWTError

# Your secret and algorithm—should be identical to what's in your auth setup.
SECRET_KEY = "P7Fq3bWJVV8ejlKOsm6l6hHsK+gg0gSrCTS6ueCmXUg="
ALGORITHM = "HS256"


access_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmNWZjYTM2My0zNDI2LTRmMTMtYWFmYS0wMDM3NmM3Mzc2OGMiLCJyb2xlcyI6WyJ1c2VyIl0sInBlcm1pc3Npb25zIjpbInVzZXI6cmVhZCJdLCJlbWFpbCI6ImtodXNod2FudHNhbndhbG90LjIzY2kwNjJAYW1jZWR1Y2F0aW9uLmluIiwiZXhwIjoxNzQ3NDAwNzc4fQ.s-z6TT6QndU5qHwvUXl8FwqDfAUQK_AT4R_2jdDFLpU"

try:
    # Decoding the token
    payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
    
    # The 'sub' field typically contains the user id
    user_id = payload.get("sub")
    
    # If you also encoded username or email, retrieve them:
    username = payload.get("username")
    email = payload.get("email")
    
    print(f"User ID: {user_id}")
    print(f"Username: {username}")  # if included in token
    print(f"Email: {email}")        # if included in token
    
except JWTError as e:
    print("Invalid token:", e)