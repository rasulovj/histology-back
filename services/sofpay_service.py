import os
import aiohttp

SOFPAY_BASE = "https://sofpay.uz/api/v1"

def _key() -> str:
    return os.getenv("SOFPAY_SHOP_KEY", "")

async def create_payment(amount: int, description: str = "Premium"):
    """Create a SofPay transaction. Returns transaction dict or None."""
    key = _key()
    if not key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{SOFPAY_BASE}/transaction/create",
                headers={"X-Shop-Key": key, "Content-Type": "application/json"},
                json={"amount": amount, "description": description},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                print(f"🧾 SofPay create response: {data}")
                if data.get("success"):
                    return data.get("transaction")
    except Exception as e:
        print(f"❌ SofPay create_payment error: {e}")
    return None

async def check_payment(tx_id: str):
    """Returns normalized status: 'paid', 'pending', 'cancelled', or None on error."""
    key = _key()
    if not key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SOFPAY_BASE}/transaction/{tx_id}",
                headers={"X-Shop-Key": key, "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                print(f"🧾 SofPay check response: {data}")

                tx = data.get("transaction") or data
                raw_status = (tx.get("status") or "").lower().strip()

                # Normalize various possible status values from SofPay
                if raw_status in ("paid", "success", "completed", "successful", "confirmed"):
                    return "paid"
                if raw_status in ("cancelled", "canceled", "failed", "rejected"):
                    return "cancelled"
                if raw_status in ("pending", "waiting", "created", "new"):
                    return "pending"

                # If success=true at top level and no nested transaction, treat as paid
                if data.get("success") and not data.get("transaction"):
                    return "paid"

                print(f"⚠️ Unknown SofPay status: '{raw_status}'")
                return "pending"
    except Exception as e:
        print(f"❌ SofPay check_payment error: {e}")
    return None
