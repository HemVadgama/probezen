import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

export async function content(): Promise<string | undefined> {
  const response = await client.responses.create({ model: "gpt-5", input: "hello" });
  if (response.output_text) {
    return response.output_text.toLowerCase();
  }
  return undefined;
}
