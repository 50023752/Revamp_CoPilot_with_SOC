import os
import pandas as pd
import glob
from google.cloud import bigquery
from config.settings import settings

# 1. Setup Client
# Force a fresh client connection
client = bigquery.Client(project=settings.gcp_project_id)
table_id = f"{settings.gcp_project_id}.{settings.logging_dataset}.evaluation_history_v5"

# # 2. Find the latest Stress Test CSV
# list_of_files = glob.glob('reports/bq_failed_*.csv') 
# if not list_of_files:
#     print("❌ No CSV files found in 'reports/' folder.")
#     exit()

# latest_file = max(list_of_files, key=os.path.getctime)
# print(f"📄 Found Report: {latest_file}")

# # 3. Load & Upload
# try:
#     df = pd.read_csv(latest_file)
    
#     # Ensure Timestamp format
#     if 'timestamp' in df.columns:
#         df['timestamp'] = pd.to_datetime(df['timestamp'])
    
#     # Convert object columns to string to satisfy BQ
#     for col in df.select_dtypes(include=['object']).columns:
#         df[col] = df[col].astype(str)

#     # Configure Safe Upload (No Partitioning enforcement to avoid errors)
#     job_config = bigquery.LoadJobConfig(
#         write_disposition="WRITE_APPEND",
#         schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]
#     )

#     print(f"📤 Uploading {len(df)} rows to {table_id}...")
#     job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
#     job.result() # Wait for completion
    
#     print("✅ Success! Data recovered and uploaded.")

# except Exception as e:
#     print(f"❌ Upload Failed: {e}")


## Upload bulk files at once

# 2. Find ALL failed CSVs
list_of_files = glob.glob('reports/bq_failed_*.csv') 

if not list_of_files:
    print("❌ No CSV files found in 'reports/' folder.")
    exit()

print(f"📄 Found {len(list_of_files)} files to process.")

try:
    # 3. Read and Combine all files
    all_dfs = []
    for filename in list_of_files:
        try:
            df_temp = pd.read_csv(filename)
            all_dfs.append(df_temp)
            print(f"  - Queued: {filename} ({len(df_temp)} rows)")
        except Exception as e:
            print(f"  ⚠️ Error reading {filename}: {e}")

    if not all_dfs:
        print("❌ No valid data extracted from files.")
        exit()

    # Combine into one DataFrame
    final_df = pd.concat(all_dfs, ignore_index=True)
    print(f"📦 Total rows to upload: {len(final_df)}")

    # 4. Process Data (Apply logic to the combined dataset)
    # Ensure Timestamp format
    if 'timestamp' in final_df.columns:
        final_df['timestamp'] = pd.to_datetime(final_df['timestamp'])
    
    # Convert object columns to string to satisfy BQ
    for col in final_df.select_dtypes(include=['object']).columns:
        final_df[col] = final_df[col].astype(str)

    # 5. Configure Safe Upload
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]
    )

    print(f"📤 Uploading to {table_id}...")
    job = client.load_table_from_dataframe(final_df, table_id, job_config=job_config)
    job.result() # Wait for completion
    
    print("✅ Success! All files recovered and uploaded.")

except Exception as e:
    print(f"❌ Bulk Upload Failed: {e}")