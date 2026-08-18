import requests
from bs4 import BeautifulSoup

fetched_pandas_v2 = {
    "changelog_v2.0.0": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.0.html", "filename": "data/raw_changelog_v2.0.0.html"},
    "changelog_v2.0.1": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.1.html", "filename": "data/raw_changelog_v2.0.1.html"},
    "changelog_v2.0.2": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.2.html", "filename": "data/raw_changelog_v2.0.2.html"},
    "changelog_v2.0.3": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.3.html", "filename": "data/raw_changelog_v2.0.3.html"},
    "migration_guide_v2": {"url": "https://pandas.pydata.org/pandas-docs/version/2.3/user_guide/copy_on_write.html", "filename": "data/raw_copy_on_write_guide_v2.html"}
}

def fetch(url, filename):
    try:
        response = requests.get(url, timeout = 10)
    except requests.exceptions.RequestException as e:
        print(f"Failed to reach {url}. Error: {e}")
    else:
        if response.status_code == 200:
            with open(filename, "w") as file:
                file.write(response.text)
                print("File downloaded successfully.")
        else:
            print(f"Failed to fetch data. Status code: {response.status_code}")

def clean(filename):
    with open(filename, "r") as input_file:
        raw_html = input_file.read()
        soup = BeautifulSoup(raw_html, "html.parser")

        # remove breadcrumb nav - not real content
        breadcrumb = soup.find("div", class_="bd-header-article")
        breadcrumb.decompose()

        # pull just the real article content, tags stripped
        main_content = soup.find("main")
        clean_text = main_content.get_text(separator=" ", strip=True)

        # save to a new file - never overwrite the raw HTML
        clean_filename = filename.replace("data/raw_", "data/clean_")
        clean_filename = clean_filename.replace("html", "txt")

        with open(clean_filename, "w") as output_file:
            output_file.write(clean_text)
            print(clean_text[:500])

for source_name, source in fetched_pandas_v2.items():  # was "key, value" - names now say what they hold
    fetch(source["url"], source["filename"])

for source_name, source in fetched_pandas_v2.items():
    clean(source["filename"])


    