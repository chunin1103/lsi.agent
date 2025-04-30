import pandas as pd

# # # DATA TRANSFORMATION
# ------------------------------------------------
# # Read data from the Excel file
# file_path = './keywords_seo.xlsx'
# data = pd.read_excel(file_path, sheet_name = "sheet1", usecols="L:L", na_filter = False)

# # Rename the column (optional)
# data.rename(columns={'Branding.1': 'branding'}, inplace=True)

# # Remove rows that are empty or just whitespace in "Branding.1"
# data = data[data["branding"].str.strip() != ""]

# # Display the 'Branding' column
# print(data)

# # Convert to JSON string
# json_string = data.to_json(orient='records', force_ascii=False)  # force_ascii=False keeps Unicode characters as-is
# print(json_string)

# data.to_json('keywords.json', orient='records', force_ascii=False)
# ------------------------------------------------

# # # Main Process
# ------------------------------------------------
import pymongo
import requests
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
HF_TOKEN = os.getenv("HF_TOKEN")

# MongoDB client setup
client = pymongo.MongoClient(MONGO_URI)

db = client.SEO_keywords
collection = db.branding

items = collection.find().limit(5)

embedding_url = "https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-MiniLM-L6-v2"


# replace the param with the text field within column
def generate_embedding(branding: str) -> list[float]:

  response = requests.post(
    embedding_url,
    headers={"Authorization": f"Bearer {HF_TOKEN}"},
    json={"inputs": branding})

  if response.status_code != 200:
    raise ValueError(f"Request failed with status code {response.status_code}: {response.branding}")

  return response.json()

# # Generate Embeddings for all the documents in the collection, comment when done
# for doc in collection.find({'branding':{"$exists": True}}).limit(300):
#   doc['text_embedding_hf'] = generate_embedding(doc['branding'])
#   collection.replace_one({'_id': doc['_id']}, doc)


query = "What are the most common keywords within this dataset?"
print(f"Query: {query}\n")

# index field needs to be setup on Mongo Atlas Search
results = collection.aggregate([
    {"$vectorSearch": 
      {
        "queryVector": generate_embedding(query),
        "path": "text_embedding_hf",
        "numCandidates": 50,
        "limit": 4,
        "index": "branding_index",
      }
    }
]);

# Output results
for document in results:
    print(f'Results: {document["branding"]}')

