import asyncio, json
from playwright.async_api import async_playwright

URL = "https://www.cian.ru/cat.php?deal_type=sale&engine_version=2&offer_type=flat&region=1&room2=1&maxprice=20000000&district%5B0%5D=42"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(locale="ru-RU")
        for attempt in (1, 2):
            resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(8000)
            html = await page.content()
            title = await page.title()
            print(json.dumps({
                "attempt": attempt,
                "status": resp.status if resp else None,
                "title": title,
                "captcha": "captcha" in html.lower()[:5000] or "Captcha" in title,
                "real_page": "_cianConfig" in html or "__NEXT_DATA__" in html,
                "len": len(html),
            }, ensure_ascii=False))
        r = await page.evaluate("""async () => {
          const q={jsonQuery:{_type:"flatsale",engine_version:{type:"term",value:2},region:{type:"terms",value:[1]},room:{type:"terms",value:[2]},geo:{type:"geo",value:[{type:"district",id:42}]},page:{type:"term",value:1}}};
          try{const r=await fetch("https://api.cian.ru/search-offers/v2/search-offers-desktop/",{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify(q)});
          const t=await r.text();return {status:r.status,json:t.startsWith("{"),head:t.slice(0,120)}}catch(e){return {error:String(e)}}
        }""")
        print("API из страницы:", json.dumps(r, ensure_ascii=False))
        await b.close()

asyncio.run(main())
