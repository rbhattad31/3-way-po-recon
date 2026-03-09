import os
import json
import re
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from SQL_Queries import get_grn_details

from openai import AzureOpenAI
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential

load_dotenv()

AZURE_OPENAI_API_KEY = ""
AZURE_OPENAI_ENDPOINT = ""
AZURE_OPENAI_API_VERSION = ""
AZURE_OPENAI_DEPLOYMENT_NAME = ""

# Document Intelligence credentials
DOC_INTELLIGENCE_KEY = ""
DOC_INTELLIGENCE_ENDPOINT = ""

pdf_directory_invoice = r""
pdf_directory_po = r""

############################################################
# PROMPTS
############################################################

INVOICE_PROMPT = """
Extract the following vendor-related fields from the above text.

Return the output strictly in JSON format with the following fields:

{
"Vendor Name":"",
"Vendor Code",
"PAN Number":"",
"GSTIN Number":"",
"Udyog AADHAR Registration Number (MSME)": "",
"Udyog AADHAR Certificate Date (MSME)": "",
"ECC Number": "",
"AADHAR (Individual)": "",
"Street/HouseNo.": "",
"Street": "",
"Building": "",
"City": "",
"District": "",
"State": "",
"Country": "",
"PO No": "",
"PO Box": "",
"Email Id's": "",
"Mobile/Landline numbers": "",
"FAX No.": "",
"Region": "",
"Country Code": "",
"Bank Account Name/Beneficiary Name": "",
"Bank Account Number":"",
"Bank Name": "",
"IFSC Code": "",
"SWIFT Code":"",
"IBAN Number":"",
"Bank Address": "",
"Bank Country": "",
"Payment Terms": "",
"Invoice Number":"",
"Invoice Date":"",
"Invoice Currency":"",
"Invoice Quantity",
"HSN Code":"",
"IRN No":"",
"GRN No":"",
"GRN Date":"",
"Description of Goods or Services":"",
"Basic Amount":"",
"CGST":"",
"SGST":"",
"IGST":"",
"Total Invoice Value":""
}

If any value is not found return empty string.
"""

PO_PROMPT = """
Extract the following PO-related fields from the above text.

Return the output strictly in JSON format with the following fields:

{
"PO Number":"",
"PO Date":"",
"Vendor Code":"",
"Vendor Name": "",
"PO Line Number": "",
"Items Code": "",
"Item": "",
"Quantity": "",
"Total PO Value": ""
}

If any value is not found return empty string.
"""

############################################################
# DOCUMENT INTELLIGENCE
############################################################

def extract_text(pdf_path):

    client = DocumentAnalysisClient(
        endpoint=DOC_INTELLIGENCE_ENDPOINT,
        credential=AzureKeyCredential(DOC_INTELLIGENCE_KEY)
    )

    with open(pdf_path, "rb") as f:

        poller = client.begin_analyze_document(
            "prebuilt-document",
            document=f
        )

    result = poller.result()

    text = ""

    for page in result.pages:
        for line in page.lines:
            text += line.content + "\n"

    return text


############################################################
# OPENAI
############################################################

def extract_using_openai(text, prompt):

    client = AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT
    )

    final_prompt = f"""
{prompt}

Document Text:
{text}

Return ONLY JSON
"""

    response = client.chat.completions.create(

        model=AZURE_OPENAI_DEPLOYMENT_NAME,

        messages=[
            {
                "role": "user",
                "content": final_prompt
            }
        ]
    )

    return response.choices[0].message.content


############################################################
# CLEAN JSON
############################################################

def clean_json(response):

    response = response.strip()

    response = response.replace("```json", "")
    response = response.replace("```", "")

    match = re.search(r"\{.*\}", response, re.DOTALL)

    if match:
        try:
            return json.loads(match.group())
        except:
            return {}

    return {}


############################################################
# PROCESS FOLDER
############################################################

def process_folder(pdf_directory, prompt, output_name):

    pdf_files = list(Path(pdf_directory).rglob("*.pdf"))

    if not pdf_files:
        print(f"\nNo files found in {pdf_directory}")
        return

    results = []

    for pdf in pdf_files:

        try:

            print("Processing:", pdf)

            text = extract_text(str(pdf))

            ai_response = extract_using_openai(text, prompt)

            json_data = clean_json(ai_response)

            json_data["File Name"] = pdf.name

            results.append(json_data)

        except Exception as e:

            print("Error processing", pdf, e)

    save_to_excel(pdf_directory, results, output_name)


############################################################
# SAVE EXCEL
############################################################

def save_to_excel(pdf_directory, data, output_name):

    output_folder = os.path.join(pdf_directory, "Output")

    os.makedirs(output_folder, exist_ok=True)

    file_path = os.path.join(output_folder, output_name)

    df = pd.DataFrame(data)

    df.to_excel(file_path, index=False)

    print(f"\nExcel saved at: {file_path}")

############################################################
# RECONCILIATION (INVOICE vs PO)
############################################################

def create_reconciliation(invoice_dir, po_dir):

    invoice_file = os.path.join(invoice_dir, "Output", "invoice_output.xlsx")
    po_file = os.path.join(po_dir, "Output", "po_output.xlsx")

    if not os.path.exists(invoice_file) or not os.path.exists(po_file):
        print("Invoice or PO file not found for reconciliation")
        return

    invoice_df = pd.read_excel(invoice_file)
    po_df = pd.read_excel(po_file)

    result_rows = []

    for _, inv in invoice_df.iterrows():

        invoice_no = inv.get("Invoice Number", "")
        po_no = inv.get("PO No", "")
        vendor = inv.get("Vendor Name", "")
        inv_amount = inv.get("Total Invoice Value", "")
        inv_qty = inv.get("Invoice Quantity", "")

        match = po_df[po_df["PO Number"] == po_no]

        if not match.empty:

            po_row = match.iloc[0]

            po_qty = po_row.get("Quantity", "")
            po_amount = po_row.get("Total PO Value", "")
            po_vendor = po_row.get("Vendor Name", "")

            # Fetch GRN details from MySQL
            grn_no = ""
            grn_qty = ""
            grn_amount = ""
            grn_vendor = ""

            grn_recon_status = ""
            grn_remarks_text = ""
            grn_result = get_grn_details(po_no)

            if grn_result:
                grn_no = grn_result.get("grn_number", "")
                grn_qty = grn_result.get("received_quantity", "")
                grn_amount = grn_result.get("grn_amount", "")
                grn_vendor = grn_result.get("vendor_name", "")
            else:
                grn_no = ""
                grn_qty = ""
                grn_amount = ""
                grn_vendor = ""

            remarks = []
            match_count = 0

            # ---------------- Vendor Name Check for PO ----------------
            if str(vendor).strip().lower() == str(po_vendor).strip().lower():
                match_count += 1
            else:
                remarks.append("Vendor Name mismatch")

            # ---------------- Quantity Check for PO ----------------
            if str(inv_qty).strip() == str(po_qty).strip():
                match_count += 1
            else:
                remarks.append("Quantity mismatch")

            # ---------------- Amount Check for PO ----------------
            if str(inv_amount).strip() == str(po_amount).strip():
                match_count += 1
            else:
                remarks.append("Amount mismatch")

            # ---------------- Final PO Reconciliation Status ----------------
            if match_count == 3:
                po_recon_status = "Matched"
                po_remarks = ""

            elif match_count == 0:
                po_recon_status = "Unmatched"
                po_remarks = ", ".join(remarks)

            else:
                po_recon_status = "Partially_Matched"
                po_remarks = ", ".join(remarks)
            grn_remarks = []
            grn_match_count = 0

            # -------- Vendor Name Check for GRN --------
            if str(vendor).strip().lower() == str(grn_vendor).strip().lower():
                grn_match_count += 1
            else:
                grn_remarks.append("Vendor Name mismatch")

            # -------- Quantity Check for GRN --------
            if str(inv_qty).strip() == str(grn_qty).strip():
                grn_match_count += 1
            else:
                grn_remarks.append("Quantity mismatch")

            # -------- Amount Check for GRN --------
            if str(inv_amount).strip() == str(grn_amount).strip():
                grn_match_count += 1
            else:
                grn_remarks.append("Amount mismatch")

            # -------- Final GRN Status --------
            if grn_match_count == 3:
                grn_recon_status = "Matched"
                grn_remarks_text = ""

            elif grn_match_count == 0:
                grn_recon_status = "Unmatched"
                grn_remarks_text = ", ".join(grn_remarks)

            else:
                grn_recon_status = "Partially_Matched"
                grn_remarks_text = ", ".join(grn_remarks)

            status = "MATCHED"

        else:

            status = "UNMATCHED"

            po_no = "Unavailable"
            po_qty = ""
            po_amount = ""

            grn_no = ""
            grn_qty = ""

            po_recon_status = "Unmatched"
            po_remarks = "PO Number not found"

            # ADD THESE LINES
            grn_recon_status = "Unmatched"
            grn_remarks_text = "GRN cannot be validated because PO not found"

        result_rows.append({
            "Invoice No": invoice_no,
            "PO No": po_no,
            "GRN No": grn_no,
            "Vendor": vendor,
            "PO Qty": po_qty,
            "GRN Qty": grn_qty,
            "Inv Qty": inv_qty,
            "PO Amount": po_amount,
            "Inv Amount": inv_amount,
            "PO_ReConStatus": po_recon_status,
            "PO_Remarks": po_remarks,
            "GRN_ReConStatus": grn_recon_status,
            "GRN_Remarks": grn_remarks_text,
            "Status": status
        })

    result_df = pd.DataFrame(result_rows)

    output_file = os.path.join(invoice_dir, "Output", "reconciliation_output.xlsx")

    result_df.to_excel(output_file, index=False)

    print("\nReconciliation file created at:", output_file)



############################################################
# MAIN
############################################################

if __name__ == "__main__":

    print("\nChecking Invoice Folder...")

    process_folder(
        pdf_directory_invoice,
        INVOICE_PROMPT,
        "invoice_output.xlsx"
    )

    print("\nChecking PO Folder...")

    process_folder(
        pdf_directory_po,
        PO_PROMPT,
        "po_output.xlsx"
    )

    print("\nCreating Reconciliation File...")

    create_reconciliation(
        pdf_directory_invoice,
        pdf_directory_po
    )

    print("\nProcessing Completed")