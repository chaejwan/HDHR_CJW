"""새 공고를 이메일로 알린다 (표준 라이브러리 smtplib)."""

from __future__ import annotations

import html as html_mod
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate


class NotifyError(Exception):
    """메일 발송 실패."""


def resolve_password(email_cfg: dict) -> str:
    """설정에 적어 둔 비밀번호, 없으면 환경변수에서 읽는다."""
    password = (email_cfg.get("password") or "").strip()
    if password:
        return password
    env_name = (email_cfg.get("password_env") or "").strip()
    return os.environ.get(env_name, "") if env_name else ""


def render(results: list, subject_prefix: str = "[채용 알림]", alerts: list = None):
    """확인 결과(사이트별)를 제목/텍스트/HTML 로 만든다."""
    alerts = alerts or []
    problems = [a for a in alerts if not a.get("recovered")]
    with_new = [r for r in results if r.get("new_items")]
    total = sum(len(r["new_items"]) for r in with_new)
    if total == 0 and problems:
        subject = f"{subject_prefix} 사이트 점검 필요 {len(problems)}건"
    elif len(with_new) == 1:
        subject = f"{subject_prefix} {with_new[0]['site_name']} 새 공고 {total}건"
    else:
        subject = f"{subject_prefix} 새 공고 {total}건 ({len(with_new)}개 사이트)"
    if total and problems:
        subject += f" · 점검 필요 {len(problems)}건"

    text_lines = []
    html_parts = [
        "<div style=\"font-family:'Malgun Gothic',AppleSDGothicNeo-Regular,sans-serif;"
        "font-size:14px;color:#1f2328;line-height:1.6\">",
    ]
    if total:
        html_parts.append(f"<h2 style='margin:0 0 16px'>새로 올라온 공고 {total}건</h2>")
    elif problems:
        html_parts.append("<h2 style='margin:0 0 16px'>사이트 점검이 필요합니다</h2>")
    for result in with_new:
        link = result.get("site_link") or result["site_url"]
        text_lines.append(f"[{result['site_name']}] {link}")
        html_parts.append(
            f"<h3 style='margin:20px 0 8px'>{html_mod.escape(result['site_name'])}"
            f" <a href='{html_mod.escape(link)}' "
            "style='font-size:12px;font-weight:400;color:#0969da'>사이트 열기</a></h3><ul style='padding-left:18px'>"
        )
        for item in result["new_items"]:
            title = item.get("title") or "(제목 없음)"
            url = item.get("url") or link
            date = item.get("date") or ""
            text_lines.append(f"  - {title}{(' (' + date + ')') if date else ''}\n    {url}")
            html_parts.append(
                f"<li style='margin-bottom:6px'><a href='{html_mod.escape(url)}' "
                f"style='color:#0969da;text-decoration:none'>{html_mod.escape(title)}</a>"
                + (f" <span style='color:#656d76'>{html_mod.escape(date)}</span>" if date else "")
                + "</li>"
            )
        html_parts.append("</ul>")
        text_lines.append("")

    if alerts:
        text_lines.append("")
        text_lines.append("[점검 필요]" if problems else "[정상 복구]")
        html_parts.append("<h3 style='margin:24px 0 8px;color:#bc4c00'>사이트 점검 안내</h3>"
                          "<ul style='padding-left:18px'>")
        for alert in alerts:
            mark = "정상 복구" if alert.get("recovered") else "점검 필요"
            line = f"  - [{mark}] {alert['site_name']}: {alert['label']} — {alert['detail']}"
            text_lines.append(line)
            text_lines.append(f"    {alert.get('site_link') or alert.get('site_url', '')}")
            color = "#1a7f37" if alert.get("recovered") else "#bc4c00"
            html_parts.append(
                f"<li style='margin-bottom:6px'><b style='color:{color}'>[{mark}]</b> "
                f"<a href='{html_mod.escape(alert.get('site_link') or alert.get('site_url', ''))}' "
                f"style='color:#0969da;text-decoration:none'>{html_mod.escape(alert['site_name'])}</a> — "
                f"{html_mod.escape(alert['label'])}<br />"
                f"<span style='color:#656d76'>{html_mod.escape(alert['detail'])}</span></li>"
            )
        html_parts.append("</ul>")

    errors = [r for r in results if r.get("status") == "error"]
    if errors:
        text_lines.append("확인하지 못한 사이트:")
        html_parts.append("<h3 style='margin:20px 0 8px;color:#bc4c00'>확인하지 못한 사이트</h3><ul style='padding-left:18px'>")
        for result in errors:
            text_lines.append(f"  - {result['site_name']}: {result.get('error', '')}")
            html_parts.append(
                f"<li>{html_mod.escape(result['site_name'])}: "
                f"{html_mod.escape(result.get('error', ''))}</li>"
            )
        html_parts.append("</ul>")

    html_parts.append(
        "<p style='margin-top:24px;color:#656d76;font-size:12px'>"
        "채용공고 모니터가 자동으로 보낸 메일입니다.</p></div>"
    )
    return subject, "\n".join(text_lines).strip() + "\n", "".join(html_parts)


def send(email_cfg: dict, recipients: list, subject: str, text_body: str, html_body: str = "") -> None:
    """SMTP 로 메일을 보낸다. 실패하면 NotifyError."""
    recipients = [addr for addr in (recipients or []) if addr]
    if not recipients:
        raise NotifyError("수신 이메일이 비어 있습니다.")
    host = (email_cfg.get("smtp_host") or "").strip()
    if not host:
        raise NotifyError("SMTP 서버 주소가 비어 있습니다.")
    port = int(email_cfg.get("smtp_port") or 587)
    security = email_cfg.get("security") or "starttls"
    username = (email_cfg.get("username") or "").strip()
    password = resolve_password(email_cfg)
    sender = (email_cfg.get("from_addr") or username or recipients[0]).strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content(text_body or "")
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise NotifyError(
            "SMTP 로그인 실패입니다. 아이디/비밀번호를 확인하세요. "
            "(Gmail 은 2단계 인증 후 만든 '앱 비밀번호'를 써야 합니다.) "
            f"원문: {exc.smtp_error.decode('utf-8', 'replace') if isinstance(exc.smtp_error, bytes) else exc}"
        ) from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise NotifyError(f"메일 발송 실패: {exc}") from exc


def send_test(email_cfg: dict, recipients: list) -> None:
    subject = f"{email_cfg.get('subject_prefix') or '[채용 알림]'} 테스트 메일"
    send(
        email_cfg,
        recipients,
        subject,
        "채용공고 모니터 테스트 메일입니다. 이 메일이 보이면 설정이 정상입니다.\n",
        "<p>채용공고 모니터 <b>테스트 메일</b>입니다. 이 메일이 보이면 설정이 정상입니다.</p>",
    )
