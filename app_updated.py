from flask import Flask, request, jsonify
import re
import os
import boto3
from langchain.prompts.prompt import PromptTemplate
from langchain_community.vectorstores import FAISS
from langchain.chains import LLMChain
from langchain_aws import BedrockEmbeddings, ChatBedrock
from langchain_core.documents import Document
from requests.auth import HTTPBasicAuth
import functools
print = functools.partial(print, flush=True)
import requests
from requests.auth import HTTPBasicAuth
import json
from langchain.text_splitter import RecursiveCharacterTextSplitter

app = Flask(__name__)

S3_BUCKET = "confluence-llm-storage"
S3_INDEX_PREFIX = "vectorstore/faiss_index/"
LOCAL_INDEX_DIR = "faiss_index"
CONFLUENCE_DOMAIN = "rajkamalsir1978"
CONFLUENCE_EMAIL = "rajkamalsir1978@gmail.com"
CONFLUENCE_API_TOKEN = "ATATT3xFfGF0Pnzpo7h07bCJgyC4fE19JTmq_UafOVCJ-eiBPdPSS9S8Jg8trG4y6jdH43W_EXvP6D_HEYbAOfNWP72cQPXqLApaysZN9UK3WRYMBy2dmX5nAElgjptK1LHPZE2EdQ6NI53XRilq0LfYpGa4EFY92StRu>
SLACK_BOT_TOKEN = "xoxb-9166583898231-9174515064355-OCou0DBSeLdQCAZ6vbdKlUii"
S3_PREFIX = "confluence/Temp/"

JIRA_EMAIL = "rajkamalsir1978@gmail.com"
JIRA_API_TOKEN = "ATATT3xFfGF0Pnzpo7h07bCJgyC4fE19JTmq_UafOVCJ-eiBPdPSS9S8Jg8trG4y6jdH43W_EXvP6D_HEYbAOfNWP72cQPXqLApaysZN9UK3WRYMBy2dmX5nAElgjptK1LHPZE2EdQ6NI53XRilq0LfYpGa4EFY92StRuLoYTsA>
SPACE_KEY = "Temp"  # Replace with your actual space key

def send_message_to_slack(channel, text):
    print(f"📤 Sending message to Slack channel {channel}: {text}")
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}"
    }
    data = {
        "channel": channel,
        "text": text
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=5)
        print("✅ Slack API response:", response.text)
        return response.json()
    except Exception as e:
        print("❌ Error sending Slack message:", str(e))
        return {"error": str(e)}

def create_or_update_confluence_page(parent_id, title, body):
    print(f"🔄 Creating or updating Confluence page: {title}")
    search_url = f"https://{CONFLUENCE_DOMAIN}.atlassian.net/wiki/rest/api/content?title={title}&spaceKey={SPACE_KEY}"
    auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    try:
        # 🔍 Search for existing page
        search_res = requests.get(search_url, auth=auth, headers=headers)
        if search_res.status_code != 200:
            print(f"❌ Failed to search for existing page: {search_res.text}")
            return False

        existing_pages = search_res.json().get("results", [])

        if existing_pages:
            # ✏️ Update existing page
            page = existing_pages[0]
            page_id = page["id"]

            # 🔄 Fetch current version to prevent conflict
            version_url = f"https://{CONFLUENCE_DOMAIN}.atlassian.net/wiki/rest/api/content/{page_id}?expand=version"
            version_res = requests.get(version_url, auth=auth, headers=headers)
            if version_res.status_code != 200:
                print(f"❌ Failed to get page version: {version_res.text}")
                return False

            current_version = version_res.json().get("version", {}).get("number", 1)

            update_url = f"https://{CONFLUENCE_DOMAIN}.atlassian.net/wiki/rest/api/content/{page_id}"
            data = {
                "version": {"number": current_version + 1},
                "title": title,
                "type": "page",
                "body": {
                    "storage": {
                        "value": body,
                        "representation": "storage"
                    }
                },
                "ancestors": [{"id": parent_id}]
            }
            update_res = requests.put(update_url, auth=auth, headers=headers, data=json.dumps(data))
            if update_res.status_code == 200:
                print(f"✅ Updated page ID {page_id}")
                return True
            else:
                print(f"❌ Failed to update page: {update_res.text}")
                return False

        else:
            # 🆕 Create new page
            create_url = f"https://{CONFLUENCE_DOMAIN}.atlassian.net/wiki/rest/api/content"
            data = {
                "type": "page",
                "title": title,
                "space": {"key": SPACE_KEY},
                "body": {
                    "storage": {
                        "value": body,
                        "representation": "storage"
                    }
                },
                "ancestors": [{"id": parent_id}]
            }
            create_res = requests.post(create_url, auth=auth, headers=headers, data=json.dumps(data))
            if create_res.status_code in [200, 201]:
                print(f"✅ Created new page under parent ID {parent_id}")
                return True
            else:
                print(f"❌ Failed to create page: {create_res.text}")
                return False

    except Exception as e:
        print(f"❌ Exception during create/update: {str(e)}")
        return False

def config_llm():
    session = boto3.Session(region_name="us-east-1")
    client = session.client('bedrock-runtime')
    model_kwargs = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "temperature": 0.9,
        "top_p": 1
    }
    return ChatBedrock(
        model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
        client=client,
        model_kwargs=model_kwargs
    )

def download_faiss_from_s3():
    s3 = boto3.client('s3')
    try:
        os.makedirs(LOCAL_INDEX_DIR, exist_ok=True)
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_INDEX_PREFIX)
        if 'Contents' not in response:
            return False
        for obj in response['Contents']:
            key = obj['Key']
            if key.endswith('/'):
                continue
            local_path = os.path.join(LOCAL_INDEX_DIR, os.path.basename(key))
            s3.download_file(S3_BUCKET, key, local_path)
        print("✅ Vector DB loaded from S3")
        return True
    except Exception as e:
        print(f"❌ Failed to download FAISS index from S3: {e}")
        return False

def upload_faiss_to_s3():
    s3 = boto3.client('s3')
    try:
        for filename in os.listdir(LOCAL_INDEX_DIR):
            file_path = os.path.join(LOCAL_INDEX_DIR, filename)
            s3_key = os.path.join(S3_INDEX_PREFIX, filename)
            s3.upload_file(file_path, S3_BUCKET, s3_key)
        print("✅ Uploaded vector DB to S3")
    except Exception as e:
        print(f"❌ Failed to upload FAISS index to S3: {e}")

# Load and embed non-empty .txt files from S3
def config_vector_db_s3(bucket_name="confluence-llm-storage", prefix="confluence/Temp/"):
    session = boto3.Session(region_name="us-east-1")
    bedrock = session.client('bedrock-runtime')
    s3 = session.client('s3')

    bedrock_embeddings = BedrockEmbeddings(
        client=bedrock,
        model_id="amazon.titan-embed-text-v2:0"
    )

    # Try to load vectorstore from S3
    if download_faiss_from_s3():
        return FAISS.load_local(LOCAL_INDEX_DIR, bedrock_embeddings, allow_dangerous_deserialization=True)

    # Rebuild from documents and upload
    response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    documents = []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2000,  # Keep this well below 8192 token limit
        chunk_overlap=200
    )

    for obj in response.get('Contents', []):
        key = obj['Key']
        if key.endswith(".txt"):
            file_obj = s3.get_object(Bucket=bucket_name, Key=key)
            content = file_obj['Body'].read().decode('utf-8').strip()
            if content:
                # Split content into chunks
                splits = text_splitter.split_text(content)
                for i, chunk in enumerate(splits):
                    documents.append(Document(
                        page_content=chunk,
                        metadata={"source": key, "chunk_id": i}
                    ))
            else:
                print(f"⚠️ Skipped empty file: {key}")

    vectorstore = FAISS.from_documents(documents, bedrock_embeddings)
    vectorstore.save_local(LOCAL_INDEX_DIR)
    upload_faiss_to_s3()
    return vectorstore


def extract_jira_links(text):
    # Extract all Jira-style links
    pattern = r"https:\/\/[^\s]+\/browse\/[A-Z]+-\d+"
    return list(set(re.findall(pattern, text)))

def vector_search(query):
    docs = vectorstore_faiss.similarity_search_with_score(query, k=10)

    if not docs:
        return "❌ *No matching internal documentation (Confluence or Jira) found for this query.*"

    response_blocks = []
    for doc, _ in docs:
        content = doc.page_content.strip()
        source = doc.metadata.get("source", "unknown")

        # Detect if this is a Jira issue or Confluence page
        if "jira/" in source:
            source_type = "📌 *Jira*"
            link = f"https://{CONFLUENCE_DOMAIN}.atlassian.net/browse/{source.split('/')[-1].replace('.txt', '')}"
        elif "pages/" in source:
            source_type = "📘 *Confluence*"
            page_id = source.split("/")[-1].replace(".txt", "")
            link = f"https://{CONFLUENCE_DOMAIN}.atlassian.net/wiki/spaces/{SPACE_KEY}/pages/{page_id}"
        else:
            source_type = "📄 *Internal*"
            link = ""

        # Add related Jira links inside the content (if any)
        jira_links = extract_jira_links(content)
        if jira_links:
            content += "\n\n🔗 *Referenced Jira Links:*\n" + "\n".join(jira_links)

        response_blocks.append(f"{source_type} <{link}>\n```\n{content[:2000]}\n```")

    return "\n\n".join(response_blocks)

prompt_template = PromptTemplate(
    input_variables=['input', 'info'],
    template="""
Human:
You are a helpful assistant for company employees. Use the following internal context (from Confluence or Jira) to answer the question. Do **not use external knowledge**.

📘 Guidelines:
- Only answer using the provided context below.
- Clearly mention whether the info came from **Jira** or **Confluence**.
- Include clickable **links** to Jira tickets or Confluence pages.
- Format your reply for Slack:
    - Use bullet points (•), numbered lists (1., 2., etc.), or markdown-style tables
    - Keep it clean and skimmable

<Internal Context>
{info}
</Internal Context>

{input}
Assistant:
"""
)


# Initialize model and vector store once on app startup
llm = config_llm()
vectorstore_faiss = config_vector_db_s3(bucket_name="confluence-llm-storage", prefix="confluence/pages/")
question_chain = LLMChain(llm=llm, prompt=prompt_template, output_key="answer")

# API route
@app.route("/ask", methods=["POST"])
def ask_question():
    data = request.json
    question = data.get("question", "").strip()
    channel = data.get("channel")  # Always present from Lambda

    if not question:
        return jsonify({"error": "Missing or empty 'question' in request"}), 400

    info = vector_search(question)
    result = question_chain.invoke({"input": question, "info": info})
    final_answer = result['answer']

    # ✅ Post back to Slack from EC2
    send_message_to_slack(channel, f"Q: {question}\nA: {final_answer}")

    return jsonify({"status": "success", "from": "ec2"})

def extract_plain_text_from_adf(adf_obj):
    texts = []
    def extract(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and "text" in node:
                texts.append(node["text"])
            for value in node.values():
                extract(value)
        elif isinstance(node, list):
            for item in node:
                extract(item)
    extract(adf_obj)
    return " ".join(texts)

def format_structured_fields(json_data):
    fields = {
        "Jira Key": json_data.get("key"),
        "Title": json_data.get("title"),
        "Status": json_data.get("status"),
        "Team Assigned": json_data.get("team_assigned"),
        "Work Type": json_data.get("work_type"),
        "Sprint": json_data.get("sprint"),
        "Story Point Estimate": json_data.get("story_point_estimate"),
        "Priority": json_data.get("priority"),
        "Assignee": json_data.get("assignee"),
        "Reporter": json_data.get("reporter"),
        "Jira Link": json_data.get("jira_link"),
        "Parent": json_data.get("parent")
    }
    return "\n".join(f"{k}: {v}" for k, v in fields.items() if v)

@app.route("/update-vector-db", methods=["POST"])
def update_vector_db():
    try:
        session = boto3.Session(region_name="us-east-1")
        s3 = session.client('s3')
        bedrock = session.client('bedrock-runtime')

        bedrock_embeddings = BedrockEmbeddings(
            client=bedrock,
            model_id="amazon.titan-embed-text-v2:0"
        )

        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX)

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=200,
            length_function=len
        )


        all_chunks = []

        for obj in response.get('Contents', []):
            key = obj['Key']
            if key.endswith(".txt"):
                file_obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
                content_raw = file_obj['Body'].read().decode('utf-8').strip()

                try:
                    json_data = json.loads(content_raw)
                except json.JSONDecodeError:
                    print(f"⚠️ Skipped non-JSON file: {key}")
                    continue

                prefix = format_structured_fields(json_data)
                description_text = extract_plain_text_from_adf(json_data.get("description", {}))
                full_content = f"{prefix}\n\nDescription:\n{description_text}"

                chunks = text_splitter.create_documents([full_content])
                for chunk in chunks:
                    chunk.metadata = {"source": key}
                all_chunks.extend(chunks)

        if not all_chunks:
            return jsonify({"message": "⚠️ No valid Jira content found to embed."}), 200

        # Step 2: Build vectorstore
        vectorstore = FAISS.from_documents(all_chunks, bedrock_embeddings)

        # Step 3: Save locally and upload to S3
        vectorstore.save_local(LOCAL_INDEX_DIR)
        upload_faiss_to_s3()  # make sure this function exists

        # Step 4: Update global in-memory store
        global vectorstore_faiss
        vectorstore_faiss = vectorstore

        return jsonify({"message": "✅ Vector DB updated and reloaded"}), 200

    except Exception as e:
        print(f"❌ Error during vector DB update: {e}")
        return jsonify({"error": f"Failed to update vector DB: {str(e)}"}), 500

@app.route("/create-release-notes", methods=["POST"])
def create_release_notes_rag():
    from datetime import datetime
    import re

    try:
        data = request.get_json()

        # Extract inputs
        prev_branch = data["prev_branch"]
        curr_branch = data["curr_branch"]
        curr_branch_name = curr_branch.split("/")[-1]
        llcr_link = data["llcr_link"]
        component = data["component"]
        parent_page_id = data["parent_page_id"]
        contributors = data.get("contributors", [])
        deployment_status = data.get("deployment_status", "")
        post_validation = data.get("post_release_validation", "Pending validation by QA")
        reference_page_ids = data.get("reference_page_ids", [])

        today = datetime.today().strftime("%Y-%m-%d")
        release_title = f"Release Notes – {component} - {today}"

        # Extract deployer name from deployment status
        deployer_match = re.search(r"Deployed By: ?([^\s<]+)", deployment_status)
        deployer_name = deployer_match.group(1) if deployer_match else "Unknown"

        # ✅ Helper: Generate content section using LLM
        def generate_section(prompt_template):
            query = prompt_template.format(reference_ids=", ".join(reference_page_ids))
            related_docs = vectorstore_faiss.similarity_search(query, k=10)
            info_blob = "\n\n".join([doc.page_content for doc in related_docs])
            result = question_chain.invoke({"input": query, "info": info_blob})
            return result['answer']

        # ✅ Define prompts for each section
        summary_prompt = """
Write a concise, professional 80–100 word summary describing the key changes in this release based on these references: {reference_ids}.
Guidelines:
- Do NOT mention "provided context" or "based on Jira tickets."
- Avoid links or technical jargon.
- Write in a natural, human tone for a business audience.
- Emphasize the overall impact of changes rather than listing IDs.
"""
        new_features_prompt = """
List new features from the referenced JIRA tickets: {reference_ids}.
Output strictly as:
<ul>
<li><b>[JIRA-ID]</b>: Short summary with context and link</li>
</ul>
"""
        improvements_prompt = """
Write the improvements introduced in the given references: {reference_ids}.
Follow these rules:
- Remove phrases like "based on Jira tickets" or "provided information"
- Group improvements under each Jira ID in bold
- Use proper HTML list format:
<ul>
<li><b>[JIRA-ID]: [Title]</b>
    <ul>
        <li>Improvement 1</li>
        <li>Improvement 2</li>
    </ul>
</li>
</ul>
Make it sound like a professional release note.
"""
        bug_fixes_prompt = """
Summarize bug fixes from these Jira tickets and Confluence pages: {reference_ids}.
If no bug fixes are mentioned, respond with:
<ul><li>No specific bug fixes were identified in this release. The focus was on new feature development and enhancements.</li></ul>
Otherwise, use this structure:
<ul>
<li><b>[JIRA-ID]: [Short Title]</b>
    <ul>
        <li>Bug fix description</li>
    </ul>
</li>
</ul>
"""
        docs_prompt = """
Extract documentation links from the given references: {reference_ids}.
Rules:
- Output as an HTML unordered list of hyperlinks.
- If no documentation links exist, return an empty <ul></ul>.
Format:
<ul>
<li><a href="URL">Document Name</a></li>
</ul>
"""
        post_validation_prompt = """
Provide a clear and concise list of QA post-release validation steps based on these references: {reference_ids}.
Guidelines:
- Do NOT say "based on provided context" or "I don't have enough info."
- Provide actionable test steps grouped by Jira ID.
- Use bullet points in HTML (<ul><li>...</li></ul>) under each Jira ticket heading.
Format:
<ul>
<li><b>[JIRA-ID]: [Short Title]</b>
    <ul>
        <li>Validation step 1</li>
        <li>Validation step 2</li>
    </ul>
</li>
</ul>
"""

        # ✅ Get responses from LLM
        summary_html = generate_section(summary_prompt)
        features_html = generate_section(new_features_prompt)
        improvements_html = generate_section(improvements_prompt)
        bugs_html = generate_section(bug_fixes_prompt)
        docs_html = generate_section(docs_prompt)
        post_validation_html = generate_section(post_validation_prompt)

        # ✅ Prepare contributors section
        contributors_html = ", ".join(contributors)

        # ✅ Construct Confluence body
        confluence_body = f"""
<h1>Release Notes – {component} - {today}</h1>
<p><strong>Release Version:</strong> {curr_branch_name}</p>
<p><strong>Deployed To:</strong> Development, Integration, Production</p>
<p><strong>Release Branch:</strong> <a href="{curr_branch}">{curr_branch}</a></p>
<p><strong>Release Triggered By:</strong> {deployer_name}</p>
<p><strong>LLCR Link:</strong> <a href="{llcr_link}">{llcr_link}</a></p>

<h2>📖 Summary</h2>
<p>{summary_html}</p>

<h2>✨ New Features</h2>
<ul>{features_html}</ul>

<h2>📈 Improvements</h2>
<ul>{improvements_html}</ul>

<h2>🐞 Bug Fixes</h2>
<ul>{bugs_html}</ul>

<h2>📜 Certifications</h2>
<p>Developer needs to attach the certification report. Contact: {deployer_name}</p>

<h2>📊 Coverage Report</h2>
<p>Developer to attach the code coverage report. Contact: {deployer_name}</p>

<h2>📚 Related Documentation</h2>
<ul>{docs_html}</ul>

<h2>👥 Contributors</h2>
<p>{contributors_html}</p>

<h2>🚀 Deployment Status</h2>
<p>{deployment_status}</p>

<h2>🔄 Rollback Plan</h2>
<p>Rolling back to the previous successful release: <code>{prev_branch}</code></p>

<h2>✅ Post Release Validation</h2>
<p>{post_validation_html}</p>
"""

        # ✅ Create or update Confluence page
        success = create_or_update_confluence_page(parent_page_id, release_title, confluence_body)

        return jsonify({
            "component": component,
            "title": release_title,
            "confluence_status": "updated" if success else "failed"
        }), 200

    except Exception as e:
        print(f"❌ Error in create-release-notes: {str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
    