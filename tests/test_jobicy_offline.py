from etl.sources import jobicy

PAYLOAD = {"jobs": [
    {"id": 1, "url": "https://x/1", "jobTitle": "Senior Python Dev", "companyName": "Acme &amp; Co",
     "jobGeo": "USA", "jobLevel": "Senior", "jobDescription": "<p>Python, SQL</p>",
     "pubDate": "2026-10-15 10:00:00", "annualSalaryMin": 120000, "annualSalaryMax": 180000,
     "salaryCurrency": "usd", "salaryPeriod": "yearly"},
    {"id": 1, "url": "https://x/1", "jobTitle": "dup", "companyName": "Acme"},
    {"id": 2, "jobTitle": "Sem url", "companyName": "Beta"},
    {"id": 3, "url": "https://x/3", "jobTitle": "Dev", "companyName": "Gama", "jobGeo": "Anywhere",
     "annualSalaryMin": 200, "annualSalaryMax": 100, "salaryPeriod": "yearly"},
]}

def test_processar():
    validas, rejeitadas = jobicy.processar(PAYLOAD)
    assert len(validas) == 1 and len(rejeitadas) == 2
    v = validas[0]
    assert v["empresa"] == "Acme & Co" and v["senioridade"] == "senior"
    assert v["salario_min"] == 10000.0 and v["moeda"] == "USD" and v["pais"] == "US"
    assert str(v["data_publicacao"]) == "2026-10-15"
