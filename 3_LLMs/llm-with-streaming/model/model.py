# In this example, we go through a Truss that serves an LLM, and streams the output to the client.
#
# # Why Streaming?
#
# For certain ML models, generations can take a long  time. Especially with LLMs, a long output could take
# 10 - 20 seconds to generate. However, because LLMs generate tokens in sequence, useful output can be
# made available to users sooner. To support this, in Truss, we support streaming output. In this example,
# we build a Truss that streams the output of the Falcon-7B model.
# 
# # Set up the imports and key constants
#
# In this example, we use the HuggingFace transformers library to build a text generation model.
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, GenerationConfig, TextIteratorStreamer
from typing import Dict
from threading import Thread

# We use the instruct version of the Falcon-7B model, and have some defaults
# for inference parameters.
CHECKPOINT = "tiiuae/falcon-7b-instruct"
DEFAULT_MAX_NEW_TOKENS = 150
DEFAULT_TOP_P = 0.95


# # Define the load function
#
# In the `load` function of the Truss, we implement logic
# involved in downloading the model and loading it into memory.
class Model:
    def __init__(self, **kwargs) -> None:
        self.tokenizer = None
        self.model = None

    def load(self):
        self.tokenizer = AutoTokenizer.from_pretrained(CHECKPOINT)
        # 
        self.tokenizer.pad_token = self.tokenizer.eos_token_id
        self.model = AutoModelForCausalLM.from_pretrained(
            CHECKPOINT,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
            device_map="auto",
        )
# # Define the predict function
# 
# In the `predict` function of the Truss, we implement the actual
# inference logic. The two main steps are:
# * Tokenize the input
# * Call the model's `generate` function, ensuring that we pass a
# `TextIteratorStreamer`. This is what gives us streaming output, and
# and also do this in a Thread, so that it does not block the main
# invocation.
# * Return a generator that iterates over the `TextIteratorStreamer` object
    def predict(self, request: Dict) -> Dict:
        prompt = request.pop("prompt")
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            max_length=512,
            truncation=True,
            padding=True
        )
        input_ids = inputs["input_ids"].to("cuda")

        # Instantiate the Streamer object, which we'll later use for
        # returning the output to users.
        streamer = TextIteratorStreamer(self.tokenizer)
        generation_config = GenerationConfig(
            temperature=1,
            top_p=DEFAULT_TOP_P,
            top_k=40,
        )

        # When creating the generation parameters, ensure to pass the `streamer` object
        # that we created previously.
        with torch.no_grad():
            generation_kwargs = {
                "input_ids": input_ids,
                "generation_config": generation_config,
                "return_dict_in_generate": True,
                "output_scores": True,
                "pad_token_id": self.tokenizer.eos_token_id,
                "max_new_tokens": DEFAULT_MAX_NEW_TOKENS,
                "streamer": streamer
            }

            # Spawn a thread to run the generation, so that it does not block the main
            # thread.
            thread = Thread(
                target=self.model.generate,
                kwargs=generation_kwargs
            )
            thread.start()

            # In Truss, the way to achieve streaming output is to return a generator
            # that yields content. In this example, we yield the output of the `streamer`,
            # which produces output and yields it until the generation is complete.
            #
            # We define this `inner` function to create our generator.
            def inner():
                for text in streamer:
                    yield text
                thread.join()

            return inner()