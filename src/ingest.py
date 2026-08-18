import requests

def fetch(url, data):
    response = requests.get(url)
    if response.status_code == 200:
        with open(data, "w") as file:
            file.write(response.text)
            print("File downloaded successfully.")
    else:
        print(f"Failed to fetch data. Status code: {response.status_code}")

data_to_fetch = {
    "changelog_v2.0.0": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.0.html", "filename": "data/changelog_v2.0.0.html"},
    "changelog_v2.0.1": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.1.html", "filename": "data/changelog_v2.0.1.html"},
    "changelog_v2.0.2": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.2.html", "filename": "data/changelog_v2.0.2.html"},
    "changelog_v2.0.3": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.3.html", "filename": "data/changelog_v2.0.3.html"},
    "migration_guide_v2": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/user_guide/copy_on_write.html", "filename": "data/copy_on_write_guide_v2.html"}
}

for key, value in data_to_fetch.items():
    fetch(value["url"], value["filename"])

