# 📘 Create Release Notes via cURL

```bash
curl -X POST http://ip-100-104-195-93.ec2.internal:5000/create-release-notes \
  -H "Content-Type: application/json" \
  -d '{
    "prev_branch": "https://github.com/org/repo/tree/release-1.0.0",
    "curr_branch": "https://github.com/org/repo/tree/release-1.1.0",
    "llcr_link": "https://confluence.company.com/display/LLCR",
    "component": "accountapi",
    "parent_page_id": "2228240",
    "contributors": ["Alice", "Bob", "Charlie"],
    "deployment_status": "Start Time: 2025-07-19<br>End Time: 2025-07-20<br>Deployed By: Kabil<br>Validation: Pending",
    "post_release_validation": "Pending validation",
    "reference_page_ids": ["GAD-1", "GAD-9", "GAD-7"]
  }'
