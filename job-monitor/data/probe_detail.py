"""(임시) 공고 상세 페이지 주소 형식을 알아내는 일회성 스크립트."""
from playwright.sync_api import sync_playwright

LIST_URL = "https://www.hanwhain.com/portal/apply/recruit"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-sandbox"])
    page = browser.new_context(user_agent=UA).new_page()
    page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(3000)

    texts = page.eval_on_selector_all(
        "*",
        """els => els.filter(e => e.children.length === 0 && e.textContent
             && e.textContent.trim().length > 12
             && e.textContent.includes('채용')
             && !e.textContent.includes('JavaScript'))
             .slice(0, 6).map(e => e.textContent.trim())"""
    )
    print("클릭 후보:", texts[:4])
    for target in texts[:2]:
        try:
            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            page.get_by_text(target, exact=True).first.click(timeout=15000)
            page.wait_for_timeout(4000)
            print("클릭:", target[:40])
            print("  이동한 주소:", page.url)
            print("  페이지 제목:", page.title())
        except Exception as exc:
            print("클릭 실패:", target[:30], "·", str(exc)[:120])
    browser.close()
