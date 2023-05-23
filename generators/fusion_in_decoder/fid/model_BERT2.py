# Copyright (c) Facebook, Inc. and its affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import os
from pathlib import Path
from types import MethodType

import numpy as np

import torch
from torch import nn
import torch.nn.functional as F

from transformers import BertConfig, BertModel, T5ForConditionalGeneration, PreTrainedModel

class FiDT5(T5ForConditionalGeneration):
    def __init__(self, config):
        super().__init__(config)
        self.wrap_encoder()

    def forward_(self, **kwargs):
        if 'input_ids' in kwargs:
            kwargs['input_ids'] = kwargs['input_ids'].view(kwargs['input_ids'].size(0), -1)
        if 'attention_mask' in kwargs:
            kwargs['attention_mask'] = kwargs['attention_mask'].view(kwargs['attention_mask'].size(0), -1)

        return super(FiDT5, self).forward(
            **kwargs
        )

    # We need to resize as B x (N * L) instead of (B * N) x L here
    # because the T5 forward method uses the input tensors to infer
    # dimensions used in the decoder.
    # EncoderWrapper resizes the inputs as (B * N) x L.
    def forward(self, input_ids=None, attention_mask=None, **kwargs):
        if input_ids != None:
            # inputs might have already be resized in the generate method
            if input_ids.dim() == 3:
                self.encoder.n_passages = input_ids.size(1)
            input_ids = input_ids.view(input_ids.size(0), -1)
            #print("input_ids:",len(input_ids),input_ids)    ########
        if attention_mask != None:
            attention_mask = attention_mask.view(attention_mask.size(0), -1)
        
        return super().forward(
            input_ids=input_ids,
            attention_mask=attention_mask,
            **kwargs
        )

    # We need to resize the inputs here, as the generate method expect 2D tensors
    def generate(self, input_ids, attention_mask, max_length, **kwargs):
        self.encoder.n_passages = input_ids.size(1)
        return super().generate(
            input_ids=input_ids.view(input_ids.size(0), -1),
            attention_mask=attention_mask.view(attention_mask.size(0), -1),
            max_length=max_length,
            **kwargs
        )

    def wrap_encoder(self, use_checkpoint=False):
        """ Wrap T5 encoder to obtain a Fusion-in-Decoder model. """
        self.encoder = EncoderWrapper(self.encoder, use_checkpoint=use_checkpoint)

    def unwrap_encoder(self):
        """ Unwrap Fusion-in-Decoder encoder, useful to load T5 weights. """
        self.encoder = self.encoder.encoder
        block = []
        for mod in self.encoder.block:
            block.append(mod.module)
        block = nn.ModuleList(block)
        self.encoder.block = block

    def load_t5(self, state_dict):
        self.unwrap_encoder()
        self.load_state_dict(state_dict)
        self.wrap_encoder()

    def set_checkpoint(self, use_checkpoint):
        """ Enable or disable checkpointing in the encoder. """
        for mod in self.encoder.encoder.block:
            mod.use_checkpoint = use_checkpoint

    def reset_score_storage(self):
        """ Reset score storage, only used when cross-attention scores are saved to train a retriever. """
        for mod in self.decoder.block:
            mod.layer[1].EncDecAttention.score_storage = None

    def get_crossattention_scores(self, context_mask):
        """
        Cross-attention scores are aggregated to obtain a single scalar per passage. 
        This scalar can be seen as a similarity score between the question and the input passage. 
        It is obtained by averaging the cross-attention scores obtained on the first decoded token over heads, layers, and tokens of the input passage.

        More details in Distilling Knowledge from Reader to Retriever:
        https://arxiv.org/abs/2012.04584.
        """
        scores = []
        n_passages = context_mask.size(1)
        for mod in self.decoder.block:
            scores.append(mod.layer[1].EncDecAttention.score_storage)
        scores = torch.cat(scores, dim=2)
        bsz, n_heads, n_layers, _ = scores.size()
        # batch_size, n_head, n_layers, n_passages, text_maxlength
        scores = scores.view(bsz, n_heads, n_layers, n_passages, -1)
        scores = scores.masked_fill(~context_mask[:, None, None], 0.)
        scores = scores.sum(dim=[1, 2, 4])
        ntokens = context_mask.sum(dim=[2]) * n_layers * n_heads
        scores = scores/ntokens
        return scores

    def overwrite_forward_crossattention(self):
        """ Replace cross-attention forward function, only used to save cross-attention scores. """
        for mod in self.decoder.block:
            attn = mod.layer[1].EncDecAttention
            attn.forward = MethodType(cross_attention_forward, attn)

# def position_embeddings(input_ids):
#         token_type_embeddings = nn.Embedding(num_embeddings=50, embedding_dim=768) #position_embedding
#         input_shape = input_ids.size()
#         token_type_ids = torch.zeros(input_shape, dtype=torch.long, device=input_ids.device)
#         position_embeddings_a = token_type_embeddings(token_type_ids) #position_embedding
#         return position_embeddings_a

class EncoderWrapper(torch.nn.Module):
    """ Encoder Wrapper for T5 Wrapper to obtain a Fusion-in-Decoder model. """
    def __init__(self, encoder, use_checkpoint=False):
        super().__init__()
        self.encoder = encoder
        apply_checkpoint_wrapper(self.encoder, use_checkpoint)
        self.main_input_name = "input_ids"
        #self.token_type_embeddings = nn.Embedding(num_embeddings=50, embedding_dim=768) #####
        # self.position_embeddings = position_embeddings()

    

    def forward(self, input_ids=None, attention_mask=None, **kwargs,):

        def PE(position, d_hid, device):
            a=np.array([position / np.power(10000, 2 * (hid_j // 2) / d_hid) for hid_j in range(d_hid)])
            c=np.array([])
            for i in range(d_hid):
                if i % 2 == 0:
                    b = np.sin(a[i])  # dim 2i
                else:
                    b = np.cos(a[i])  # dim 2i+1
                c = np.append(c,b)
            return torch.FloatTensor(c).to(device) #.unsqueeze(0)

        bsz, total_length = input_ids.shape
        passage_length = total_length // self.n_passages
        
        torch.set_printoptions(edgeitems=15000) #printの表示を省略しない

        input_ids = input_ids.view(bsz*self.n_passages, passage_length) 
        position_embeddings = torch.zeros([bsz*self.n_passages, passage_length, 768], dtype=torch.float, device=input_ids.device)  
        #print(input_ids.device)
        for i in range(len(position_embeddings)):                                                                                                            #####
            pe = PE(position=i, d_hid=768, device=input_ids.device) #position_embedding
            position_embeddings[i] += pe                  
        position_embeddings = torch.reshape(position_embeddings, (1, self.n_passages*passage_length, 768))
        #print(position_embeddings[0][255][:20])
        #print(position_embeddings[0][256][:20])                                 
        attention_mask = attention_mask.view(bsz*self.n_passages, passage_length)
        outputs = self.encoder(input_ids, attention_mask, **kwargs)
        #print("outputs1:",outputs)
        outputs.last_hidden_state = outputs.last_hidden_state.view(bsz, self.n_passages*passage_length, -1)
        #print("outputs.last_hidden_state:",outputs.last_hidden_state.size(),outputs.last_hidden_state)       ###############ここがembeddingしてる場所！
        outputs.last_hidden_state = outputs.last_hidden_state + position_embeddings
        #print("outputs2:",outputs)
        return outputs

    
        


class CheckpointWrapper(torch.nn.Module):
    """ Wrapper replacing None outputs by empty tensors, which allows the use of checkpointing. """
    def __init__(self, module, use_checkpoint=False):
        super().__init__()
        self.module = module
        self.use_checkpoint = use_checkpoint
    def forward(self, hidden_states, attention_mask, position_bias, **kwargs):
        if self.use_checkpoint and self.training:
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            def custom_forward(*inputs):
                output = self.module(*inputs, **kwargs)
                empty = torch.tensor(
                    [],
                    dtype=torch.float,
                    device=output[0].device,
                    requires_grad=True)
                output = tuple(x if x is not None else empty for x in output)
                return output

            output = torch.utils.checkpoint.checkpoint(
                custom_forward,
                hidden_states,
                attention_mask,
                position_bias
            )
            return tuple(x if x.size() != 0 else None for x in output)
        else:
            return self.module(hidden_states, attention_mask, position_bias, **kwargs)


def apply_checkpoint_wrapper(t5stack, use_checkpoint):
    """
    Wrap each block of the encoder to enable checkpointing.
    """
    block = []
    for mod in t5stack.block:
        wrapped_mod = CheckpointWrapper(mod, use_checkpoint)
        block.append(wrapped_mod)
    block = nn.ModuleList(block)
    t5stack.block = block


def cross_attention_forward(
        self,
        input,
        mask=None,
        kv=None,
        position_bias=None,
        past_key_value_state=None,
        head_mask=None,
        query_length=None,
        use_cache=False,
        output_attentions=False,
    ):
    """ This only works for computing cross attention over the input """
    assert(kv != None)
    assert(head_mask == None)
    assert(position_bias != None or self.has_relative_attention_bias)

    bsz, qlen, dim = input.size()
    n_heads, d_heads = self.n_heads, self.d_kv
    klen = kv.size(1)

    q = self.q(input).view(bsz, -1, n_heads, d_heads).transpose(1, 2)
    if past_key_value_state == None:
        k = self.k(kv).view(bsz, -1, n_heads, d_heads).transpose(1, 2)
        v = self.v(kv).view(bsz, -1, n_heads, d_heads).transpose(1, 2)
    else:
        k, v = past_key_value_state

    scores = torch.einsum("bnqd,bnkd->bnqk", q, k)

    if mask is not None:
       scores += mask

    if position_bias is None:
        position_bias = self.compute_bias(qlen, klen)
    scores += position_bias

    if self.score_storage is None:
        self.score_storage = scores

    attn = F.softmax(scores.float(), dim=-1).type_as(scores)
    attn = F.dropout(attn, p=self.dropout, training=self.training)

    output = torch.matmul(attn, v)
    output = output.transpose(1, 2).contiguous().view(bsz, -1, self.inner_dim)
    output = self.o(output)

    if use_cache:
        output = (output,) + ((k, v),)
    else:
        output = (output,) + (None,)

    if output_attentions:
        output = output + (attn,)

    if self.has_relative_attention_bias:
        output = output + (position_bias,)

    return output


class RetrieverConfig(BertConfig):

    def __init__(self,
                 indexing_dimension=768,
                 apply_question_mask=False,
                 apply_passage_mask=False,
                 extract_cls=False,
                 passage_maxlength=200,
                 question_maxlength=40,
                 projection=True,
                 **kwargs):
        super().__init__(**kwargs)
        self.indexing_dimension = indexing_dimension
        self.apply_question_mask = apply_question_mask
        self.apply_passage_mask = apply_passage_mask
        self.extract_cls=extract_cls
        self.passage_maxlength = passage_maxlength
        self.question_maxlength = question_maxlength
        self.projection = projection


class Retriever(PreTrainedModel):

    config_class = RetrieverConfig
    base_model_prefix = "retriever"

    def __init__(self, config, initialize_wBERT=False):
        super().__init__(config)
        assert config.projection or config.indexing_dimension == 768, \
            'If no projection then indexing dimension must be equal to 768'
        self.config = config
        if initialize_wBERT:
            self.model = BertModel.from_pretrained('bert-base-uncased')
        else:
            self.model = BertModel(config)
        if self.config.projection:
            self.proj = nn.Linear(
                self.model.config.hidden_size,
                self.config.indexing_dimension
            )
            self.norm = nn.LayerNorm(self.config.indexing_dimension)
        self.loss_fct = torch.nn.KLDivLoss()

    def forward(self,
                question_ids,
                question_mask,
                passage_ids,
                passage_mask,
                gold_score=None):
        question_output = self.embed_text(
            text_ids=question_ids,
            text_mask=question_mask,
            apply_mask=self.config.apply_question_mask,
            extract_cls=self.config.extract_cls,
        )
        bsz, n_passages, plen = passage_ids.size()
        passage_ids = passage_ids.view(bsz * n_passages, plen)
        passage_mask = passage_mask.view(bsz * n_passages, plen)
        passage_output = self.embed_text(
            text_ids=passage_ids,
            text_mask=passage_mask,
            apply_mask=self.config.apply_passage_mask,
            extract_cls=self.config.extract_cls,
        )

        score = torch.einsum(
            'bd,bid->bi',
            question_output,
            passage_output.view(bsz, n_passages, -1)
        )
        score = score / np.sqrt(question_output.size(-1))
        if gold_score is not None:
            loss = self.kldivloss(score, gold_score)
        else:
            loss = None

        return question_output, passage_output, score, loss

    def embed_text(self, text_ids, text_mask, apply_mask=False, extract_cls=False):
        text_output = self.model(
            input_ids=text_ids,
            attention_mask=text_mask if apply_mask else None
        )
        if type(text_output) is not tuple:
            text_output.to_tuple()
        text_output = text_output[0]
        if self.config.projection:
            text_output = self.proj(text_output)
            text_output = self.norm(text_output)

        if extract_cls:
            text_output = text_output[:, 0]
        else:
            if apply_mask:
                text_output = text_output.masked_fill(~text_mask[:, :, None], 0.)
                text_output = torch.sum(text_output, dim=1) / torch.sum(text_mask, dim=1)[:, None]
            else:
                text_output = torch.mean(text_output, dim=1)
        return text_output

    def kldivloss(self, score, gold_score):
        gold_score = torch.softmax(gold_score, dim=-1)
        score = torch.nn.functional.log_softmax(score, dim=-1)
        return self.loss_fct(score, gold_score)