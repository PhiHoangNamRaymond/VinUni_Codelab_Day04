"""
Lab #4: System Prompt Engineering & Tool Calling Engine
Học viên hoàn thiện các mục TODO để hoàn thành bài lab.

Kiến trúc:
  - ChatbotBaseline: LLM thuần, không dùng tool → quan sát hallucination.
  - ToolCallingAgent: Agent dùng System Prompt + 2 Tool Schemas.
"""

import json
import re
from decimal import Decimal
from typing import Dict, Any, List
from tools import TOOL_DEFINITIONS, TOOL_MAP, search_product_catalog, submit_support_ticket

# ═══════════════════════════════════════════════════════════════════════════
# TODO 1: Thiết kế SYSTEM PROMPT cấp sản xuất
# Yêu cầu: Phải chứa Persona, Core Rules, Operational Boundaries, Output Contract.
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
## PERSONA
Bạn là VinAssistant, trợ lý tư vấn sản phẩm và dịch vụ VinFast, Vinpearl.
Trả lời bằng tiếng Việt, rõ ràng và lịch sự.

## AVAILABLE TOOLS
- search_product_catalog: Tra cứu xe điện hoặc dịch vụ du lịch theo danh mục
  và mức giá tối đa tính bằng VNĐ.
- submit_support_ticket: Ghi nhận yêu cầu hỗ trợ với tên khách hàng,
  mô tả vấn đề và mức độ ưu tiên.

## CORE RULES
- Khi khách hỏi về sản phẩm hoặc giá, gọi search_product_catalog và dùng kết quả
  trả về để trả lời. Không tự đặt giá hoặc tên sản phẩm.
- Khi khách yêu cầu tạo ticket, hỏi bổ sung nếu thiếu tên hoặc mô tả vấn đề.
  Chỉ xác nhận đã tạo ticket khi công cụ trả về mã ticket.
- Nếu khách vừa muốn tra cứu vừa muốn tạo ticket, xử lý cả hai yêu cầu.
- Nếu danh mục trả về rỗng, báo không tìm thấy sản phẩm phù hợp.

## OPERATIONAL BOUNDARIES
Chỉ hỗ trợ các câu hỏi về sản phẩm, dịch vụ và hỗ trợ khách hàng của Vingroup.
Với câu hỏi ngoài phạm vi, lịch sự hướng khách về các chủ đề có thể hỗ trợ.

## OUTPUT CONTRACT
- Thought: Nêu ngắn gọn tác vụ cần xử lý.
- Action: Ghi tên công cụ và tham số, hoặc ghi "Không gọi công cụ".
- Observation: Tóm tắt kết quả khi có gọi công cụ.
- Final Answer: Trả lời khách bằng tiếng Việt. Nếu đã tạo ticket, ghi rõ mã ticket.
"""


# ═══════════════════════════════════════════════════════════════════════════
# CLASS: ChatbotBaseline
# ═══════════════════════════════════════════════════════════════════════════

class ChatbotBaseline:
    """Baseline LLM Chatbot — Không sử dụng Tool Calling hay ReAct Loop."""

    def query(self, user_input: str) -> Dict[str, Any]:
        # TODO 2: Trả về câu trả lời tĩnh (mock) hoặc gọi Gemini API 1 lượt (không dùng tool)
        # Mục tiêu: Quan sát hiện tượng bịa thông tin (hallucination)
        return {
            "answer": f"[Chatbot Baseline] Trả lời cho: {user_input}",
            "tool_calls": [],
            "status": "success",
            "mode": "mock_baseline"
        }


# ═══════════════════════════════════════════════════════════════════════════
# CLASS: ToolCallingAgent
# ═══════════════════════════════════════════════════════════════════════════

class ToolCallingAgent:
    """Agent với System Prompt Engineering & Tool Calling."""

    def __init__(self, max_iterations: int = 5):
        self.max_iterations = max_iterations
        self.trace: List[Dict[str, Any]] = []

    def run(self, user_input: str) -> Dict[str, Any]:
        self.trace = []
        query = user_input.casefold()

        needs_catalog = any(phrase in query for phrase in (
            "muốn xem", "cho tôi xem", "giá dưới", "giá tối đa", "tra cứu",
        ))
        needs_ticket = any(phrase in query for phrase in (
            "bị lỗi", "tạo ticket", "ghi nhận phản hồi", "khiếu nại",
        ))
        is_faq = not needs_catalog and not needs_ticket and any(
            phrase in query for phrase in ("bảo hành", "chính sách", "bao lâu")
        )

        intents = {
            "needs_catalog": needs_catalog,
            "needs_ticket": needs_ticket,
            "is_faq": is_faq,
        }
        self.trace.append({
            "step": "intent_detection",
            "user_input": user_input,
            "intents": intents,
        })

        actions = []
        answers = []

        if intents["needs_catalog"]:
            category = None
            if any(word in query for word in ("vinpearl", "resort", "du lịch", "khách sạn")):
                category = "du_lich"
            elif any(word in query for word in ("vinfast", "xe điện")):
                category = "xe_dien"

            if category is None:
                answers.append("Bạn muốn tra cứu xe điện hay dịch vụ du lịch?")
            else:
                arguments = {"category": category}
                price = re.search(r"(\d+(?:[.,]\d+)?)\s*(triệu|tỷ)\b", query)
                if price:
                    multiplier = {"triệu": 1_000_000, "tỷ": 1_000_000_000}[price[2]]
                    amount = Decimal(price[1].replace(",", "."))
                    arguments["max_price"] = int(amount * multiplier)
                actions.append(("search_product_catalog", arguments))

        if intents["needs_ticket"]:
            match = re.search(
                r"(?:tôi tên(?: là)?|tên tôi là)\s+([^,.;!?]+)[,.;!?]\s*(.+)",
                user_input,
                re.IGNORECASE,
            )

            if match:
                customer_name = match[1].strip()
                issue = re.split(
                    r"[.!?]|,\s*mức độ", match[2], maxsplit=1, flags=re.IGNORECASE
                )[0].strip()

                if issue:
                    priority = "medium"
                    if any(word in query for word in ("nghiêm trọng", "gấp", "mức độ cao")):
                        priority = "high"
                    elif "mức độ thấp" in query:
                        priority = "low"

                    actions.append(("submit_support_ticket", {
                        "customer_name": customer_name,
                        "issue_description": issue,
                        "priority": priority,
                    }))
                else:
                    answers.append("Bạn vui lòng mô tả vấn đề cần hỗ trợ.")
            else:
                answers.append("Bạn vui lòng cung cấp tên và mô tả vấn đề.")

        if intents["is_faq"]:
            answers.append(
                "Bạn cho mình biết mẫu xe và năm mua để tra cứu chính sách bảo hành phù hợp."
            )

        if max(1, len(actions)) > self.max_iterations:
            return {
                "answer": "Yêu cầu vượt quá số vòng xử lý cho phép.",
                "trace": self.trace,
                "iterations": 0,
                "status": "max_iterations_reached",
            }

        for action, arguments in actions:
            observation = TOOL_MAP[action](**arguments)
            self.trace.append({
                "step": "tool_call",
                "action": action,
                "arguments": arguments,
                "observation": observation,
            })

            if action == "search_product_catalog":
                if not observation:
                    answers.append("Không tìm thấy sản phẩm phù hợp.")
                elif "error" in observation[0]:
                    answers.append("Hiện chưa đọc được danh mục sản phẩm.")
                else:
                    answers.extend(
                        f"{item['name']} — {item['price_vnd']:,} VNĐ"
                        for item in observation
                    )

            elif action == "submit_support_ticket":
                answers.append(
                    f"Đã tạo ticket {observation['ticket_id']} "
                    f"cho {observation['customer_name']}."
                )

        return {
            "answer": "\n".join(answers) or "Bạn cần hỗ trợ gì thêm?",
            "trace": self.trace,
            "iterations": max(1, len(actions)),
            "status": "completed",
        }



# ═══════════════════════════════════════════════════════════════════════════
# MAIN — Chạy thử nhanh
# ═══════════════════════════════════════════════════════════════════════════

def main():
    user_query = "Tôi muốn xem xe điện VinFast giá dưới 600 triệu."

    print("=== RUNNING CHATBOT BASELINE ===")
    chatbot = ChatbotBaseline()
    print(chatbot.query(user_query))

    print("\n=== RUNNING TOOL CALLING AGENT ===")
    agent = ToolCallingAgent(max_iterations=5)
    result = agent.run(user_query)
    print("Result:", result["answer"])
    print("Trace Log:", json.dumps(agent.trace, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
