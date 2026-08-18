import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_TOKEN ?? "", { apiVersion: "2025-08-27.basil" });

export async function calculateTotal(quantity: number): Promise<number> {
  const response = await fetch("https://api.stripe.com/v1/prices");
  const product = await response.json();
  return product.price * quantity;
}

export async function customerEmail(): Promise<string> {
  const response = await fetch("https://billing.internal.example/v1/customer");
  const customer = await response.json();
  return customer.email.toLowerCase();
}

void stripe;
