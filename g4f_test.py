# example_hanoi_eats.py
import g4f
from g4ftemplate import g4f_chat     # import the sync helper

# 1) Craft your messages
messages = [
    {
        "role": "system",
        "content": "You are an expert in SEO and keyword research based on LSI technique. You have 10 years of experience in keyword research and SEO consulting.",
    },
    {
        "role": "user",
        "content": (
            """
            Tìm từ khóa gốc dựa trên phương pháp LSI cho từ khóa sau: "Nước mắm". Tránh lặp lại từ này trong danh sách từ khóa.
            """
        ),
    },
]

# 2) (Optional) choose providers or let g4f auto‑select
my_providers = [
    g4f.Provider.PollinationsAI,
    g4f.Provider.Blackbox,	
    None                # fallback to any provider that works
]

# 3) Call the model
reply = g4f_chat(
    messages,
    providers=my_providers,   # omit this arg to let g4f pick freely
    model="gpt-4o",          
    temperature=0.7,       
    timeout=120,
)

print("\nModel suggests:\n")
print(reply)
