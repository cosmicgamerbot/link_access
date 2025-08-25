import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Replace with your Sheet ID
SHEET_ID = "14etqLqNgEpJG0Z4i0TPsBykwaxVhANyxS_y0Zw46Rzk"

scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("cred.json", scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1  # assumes first sheet
