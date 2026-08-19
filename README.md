This project is made to backup major Google Sheets files, so nothing would go wrong

Intended workflow:

    ### 1. Deploy to backup server

    python -m deploy

    ### 2. Deploy creates **gsheets_backup.service** on TARGET_SERVER 

    ### 3. Each day at 00:00:00 UTC +2 (current local timezone - Europe/Prague) this service has to download fresh copy of gsheets from **gsheets_ids.txt** using credentials of google service account

    ### 4. Upon completion and verification that files are not corrupted - it should send a notification to **ADMIN** using telegram bot api 