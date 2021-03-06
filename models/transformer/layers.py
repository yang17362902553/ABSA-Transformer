import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import *
import numpy as np
import copy

import models.transformer.constants as constants

def clone_layer(layer: nn.Module, N: int):
    """Produces N identical layers
    """
    return nn.ModuleList([copy.deepcopy(layer) for _ in range(N)])


class PointWiseFCLayer(nn.Module):

    def __init__(self,
                d_input,
                d_layer,
                dropout,
                use_conv = False,
				use_bias=False) -> None:
        super(PointWiseFCLayer, self).__init__()

        self.d_input = d_input
        self.d_layer = d_layer
        self.p_dropout = dropout

        if use_conv:
            self.w_1 = nn.Conv1d(d_input, d_layer, 1) 
            self.w_2 = nn.Conv1d(d_layer, d_input, 1)      # output dimension = input dimension 
        else:
            self.w_1 = nn.Linear(self.d_input, self.d_layer, bias=use_bias)
            self.w_2 = nn.Linear(self.d_layer, self.d_input, bias=use_bias)
        self.dropout = nn.Dropout(self.p_dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        result = self.w_2(F.relu(self.w_1(x)))
        result = self.dropout(result)
        return result

    def __str__(self):
        return self.__class__.__name__

    def _get_parameters(self, indentation: str) -> str:
        result = indentation + "\tLinear Input: {0}\n".format(self.d_input)
        result += indentation + "\tLinear Size: {0}\n".format(self.d_layer)
        result += indentation + "\tDropout Rate: {0}\n".format(self.p_dropout)
        return result

    def print_model_graph(self, indentation: str) -> str:
        result = indentation + "- " + self.__str__() + ": - Parameters\n" + self._get_parameters(indentation + "\t")
        return result


class ScaledDotProductAttentionLayer(nn.Module):

    def __init__(self,
                d_k,
                d_v,
                dropout) -> None:
        """
        d_k: dimensionality of the query and key vectors    (default 64)
        d_v: dimensionality of the value vector             (default 64)
        dropout                                             (default 0.1)
        """
        super(ScaledDotProductAttentionLayer, self).__init__()

        self.d_k = d_k
        self.d_v = d_v
        self.gradientStabilizer = math.sqrt(self.d_k)

        # for a isolated scaled dot-product attention we would use this.
        # However, for multi-head attention we project a single v, k, q matrix
        # using multiple linear layers (one for each matrix and attention head) -> 3 * number of heads layers
        # self.q_matrix = nn.Sequential(nn.Linear(self.d_k, self.d_k), nn.ReLU())
        # self.k_matrix = nn.Sequential(nn.Linear(self.d_k, self.d_k), nn.ReLU())
        # self.v_matrix = nn.Sequential(nn.Linear(self.d_v, self.d_v), nn.ReLU())

        if dropout is not None:
            self.dropout = nn.Dropout(dropout)
        else:
            self.dropout = None

    def _self_attention(self,
                        x: torch.Tensor,
                        w_query: torch.Tensor,
                        w_key: torch.Tensor,
                        w_value: torch.Tensor,
                        mask: torch.Tensor =None,
                        dropout: torch.Tensor=None,
                        eps=1e-6) -> (torch.Tensor, torch.Tensor):
        """Calculates attention for one head.
        This method is the 'Scaled Dot-Product Attention mechanism from the paper.
        Corresponds to Figure 2 left
        x: input sentence (list of word embeddings of sentence)     [batch_size, num_words] (512, n)
        w_query: query matrix                                       [d_k, batch_size]       (64, 512)
        w_key: key matrix                                           [d_k, batch_size]       (64, 512)
        w_value: value matrix                                       [d_v, batch_size]       (64, 512)
        mask: mask to prevent leftward information flow             [num_words, num_words]      (n, n)
        dropout: dropout layer
        eps: epsilon value
        """

        # Step 1:   Multiply input (matrix of word embeddings of sentence) with query, key and value matrixes
        #           Result is a query, key and value vector for each word
        x_w_query = x * w_query                                 #   [d_k, num_words]            (64, n)
        x_w_key = x * w_key                                     #   [d_k, num_words]            (64, n)
        x_w_value = x * w_value                                 #   [d_v, num_words]            (64, n)

        # Step 2:   For each word in input sentence, calculate a score against the current word
        #               for w_1 in sentence
        #                   for w_2 in sentence
        #                       query_vector(w_1) * key_vector(w_2)

        #           Divide by the square root of the query/key/value matrix sizes (default 8)
        scores = torch.matmul(x_w_query * torch.t(x_w_key)) / self.gradientStabilizer   # [num_words, num_words]       

        # Step 3:   To prevent leftward information flow, we need to mask out words that the attention
        #           head is not 'allowed' to see and in the case of the encode we need to mask out the padding values
        if mask is not None:
            scores = scores.masked_fill(mask == 0, eps)

        # Step 4:   Create softmax of scores
        attention = F.softmax(scores, dim=-1)  # [num_words, num_words]     
        
        if dropout is not None:
            attention = dropout(attention)

        # Step 5:   multiply each score with their coresponding softmax score
        #           sum up all the scores -> output for one word      
        result = torch.matmul(attention, x_w_value)    # [d_v, num_words]      (64, n)
        return result, attention

    def _self_attention_multi_head(self,
                        w_query: torch.Tensor,
                        w_key: torch.Tensor,
                        w_value: torch.Tensor,
                        mask: torch.Tensor =None,
                        dropout: torch.Tensor=None,
                        eps=1e-6):
        """Calculates attention for multiple heads.
        This method is the 'Scaled Dot-Product Attention mechanism from the paper.
        Corresponds to Figure 2 left
        w_query: query matrix                                       [d_k, batch_size, n_head]       (64, 512, 8)
        w_key: key matrix                                           [d_k, batch_size, n_head]       (64, 512, 8)
        w_value: value matrix                                       [d_v, batch_size, n_head]       (64, 512, 8)
        mask: mask to prevent leftward information flow             [num_words, num_words]         (n, n)
        dropout: dropout layer
        eps: epsilon value
        """

        # Step 1:   Multiply input (matrix of word embeddings of sentence) with query, key and value matrixes
        #           Result is a query, key and value vector for each word
        # **Already done**

        # Step 2:   For each word in input sentence, calculate a score against the current word
        #               for w_1 in sentence
        #                   for w_2 in sentence
        #                       query_vector(w_1) * key_vector(w_2)

        #           Divide by the square root of the query/key/value matrix sizes (default 8)
        # bmm = batch matrix multiplication
        scores = torch.bmm(w_query, w_key.permute(0, 2, 1)) / self.gradientStabilizer   # [num_words, num_words, n_head]       

        # Step 3:   To prevent leftward information flow, we need to mask out words that the attention
        #           head is not 'allowed' to see
        if mask is not None:
            scores = scores.masked_fill(mask == 0, eps)

        # Step 4:   Create softmax of scores
        # TODO: check dimension (checked)
        attention = F.softmax(scores, dim=-1)  # [num_words, num_words, n_head]     
        
        if dropout is not None:
            attention = dropout(attention)

        # Step 5:   multiply each score with their coresponding softmax score
        #           sum up all the scores -> output for one word      
        result = torch.bmm(attention, w_value)    # [d_v, num_words]      (64, n)
        return result
        
    def forward(self, q_matrix, k_matrix, v_matrix, mask):
        return self._self_attention_multi_head(q_matrix, k_matrix, v_matrix, mask, self.dropout)

    def __str__(self):
        return self.__class__.__name__

    def _get_parameters(self, indentation: str) -> str:
        result = indentation + "\tKey - Query Size: {0}\n".format(self.d_k)
        result += indentation + "\tValue Size: {0}\n".format(self.d_v)
        return result

    def print_model_graph(self, indentation: str) -> str:
        return indentation + "- " + self.__str__() + ": - Parameters\n" + self._get_parameters(indentation + "\t")


class MultiHeadedSelfAttentionLayer(nn.Module):

    def __init__(self,
                d_k,
                d_v,
                d_model,
                n_head,
                dropout_rate,
                use_linear=True) -> None:
        """
        d_k: dimensionality of the query and key vectors
        d_v: dimensionality of the value vector
        n_head: number of attention heads
        """
        super(MultiHeadedSelfAttentionLayer, self).__init__()

        assert d_model % n_head == 0
        assert d_k * n_head == d_model

        self.d_model = d_model
        self.d_k = d_k
        self.d_v = d_v
        self.n_head = n_head

        # query, key, value projections
        # those matrices are used to transform the query, key and value matrix for each attention head
        # During forward pass these projection matrices are split so that they can be applied for each head
        # in the paper they are called W^Q, W^k and W^V
        # the projections do not use a non-linearity or bias
        # TODO: Check against other implementations
        self.query_projections = nn.Linear(self.d_model, self.d_k * self.n_head, bias=False)
        self.key_projections = nn.Linear(self.d_model, self.d_k * self.n_head, bias=False)
        self.value_projections = nn.Linear(self.d_model, self.d_v * self.n_head, bias=False)

        # one 'attention_layer' is sufficient even if the model uses multiple heads since the layer
        # only performs a forward pass without any learned parameters
        self.attention_layer = ScaledDotProductAttentionLayer(d_k, d_v, dropout_rate)

        # after concatinating the attention output of the heads this last matrix is used to project the output
        # back to the original input size so that the output of this layer can be used again for the next layer
        # The input of this layer is the output of the forward pass of head attention head, multiplied by the number of heads
        # The output should be the model dimension again, so that the input dimension of the layer 
        # input_MultiHeadedSelfAttentionLayer = output_MultiHeadedSelfAttentionLayer
        self.w_0 = nn.Linear(self.n_head * self.d_v, self.d_model, bias=False)
        
        if dropout_rate is not None:
            self.dropout = nn.Dropout(dropout_rate)
        else:
            self.dropout = None

        #self._initialize_layers(True)


    def _initialize_layers(self, normal: bool = True):
        if (normal):
            nn.init.normal(self.query_projections.weight, mean=0, std=np.sqrt(2.0 / (self.d_model + self.d_k)))
            nn.init.normal(self.key_projections.weight, mean=0, std=np.sqrt(2.0 / (self.d_model + self.d_k)))
            nn.init.normal(self.value_projections.weight, mean=0, std=np.sqrt(2.0 / (self.d_model + self.d_v)))

        nn.init.xavier_normal_(self.w_0.weight)

    def _split_heads(self, s):
        None

    def forward(self, x_queries: torch.Tensor, x_keys: torch.Tensor, x_values: torch.Tensor, mask: torch.Tensor=None) -> torch.Tensor:
        """
        x_queries: [batch_size, num_words, d_model] (100, 10, 512)
        """

        # project key, query, value for each head using the linear layers
        Q = self.query_projections(x_queries)       # [batch_size, num_words, d_model]
        K = self.key_projections(x_keys)            # [batch_size, num_words, d_model]
        V = self.value_projections(x_values)        # [batch_size, num_words, d_model]

        # split to head input dimensions
        # TODO: check dimensions
        batch_size, len_q, _ = x_queries.size()           # (batch_size, num_words)
        _, len_k, _ = x_keys.size()                 
        _, len_v, _ = x_values.size()

        Q = Q.view(batch_size, len_q, self.n_head, self.d_k)   # [batch_size, num_words, num_heads, d_k]
        K = K.view(batch_size, len_k, self.n_head, self.d_k)   # [batch_size, num_words, num_heads, d_k]
        V = V.view(batch_size, len_v, self.n_head, self.d_v)   # [batch_size, num_words, num_heads, d_v]

        # Transform Q, K and V so that the head-dimension is merged with the batch_size 
        # [batch_size, num_words, num_heads, d_k] -> [batch_size * num_heads, num_words, d_k]
        Q = Q.permute(2, 0, 1, 3).contiguous().view(-1, len_q, self.d_k) # (n*b) x lq x dk
        K = K.permute(2, 0, 1, 3).contiguous().view(-1, len_k, self.d_k) # (n*b) x lk x dk
        V = V.permute(2, 0, 1, 3).contiguous().view(-1, len_v, self.d_v) # (n*b) x lv x dv

        # apply mask
        if mask is not None:
            mask = mask.repeat(self.n_head, 1, 1)

        # perform forward pass of individual heads
        result = self.attention_layer(Q, K, V, mask=mask)

        # prepare for merge by concatenating heads
        # TODO: check
        result = result.view(self.n_head, batch_size, len_q, self.d_v)
        result = result.permute(1, 2, 0, 3).contiguous().view(batch_size, len_q, -1) # b x lq x (n*dv)

        result = self.w_0(result)

        if self.dropout is not None:
            result = self.dropout(result)

        return result
    
    def __str__(self):
        return self.__class__.__name__

    def _get_parameters(self, indentation: str) -> str:
        result = indentation + "\tModel Size: {0}\n".format(self.d_model)
        result += indentation + "\t# Heads: {0}\n".format(self.n_head)
        result += indentation + "\tValue Size: {0}\n".format(self.d_v)
        return result

    def print_model_graph(self, indentation: str) -> str:
        result = indentation + "[\n"
        result += indentation + "- " + self.__str__() + ": - Parameters\n" + self._get_parameters(indentation + "\t") + "\n"
        result += self.attention_layer.print_model_graph(indentation + "\t")
        result += indentation + "]\n"
        return result


class LayerNorm(nn.Module):

    def __init__(self, features, eps=1e-6) -> None:
        """Applies layer normalization from Jimmy Lei Ba et al. Layer normalization
        """
        super(LayerNorm, self).__init__()
        self.gamma = nn.Parameter(torch.ones(features))
        self.beta = nn.Parameter(torch.zeros(features))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        mean = x.mean(-1, keepdim=True)
        std = x.std(-1, keepdim=True)
        return self.gamma * (x - mean) / (std + self.eps) + self.beta

    def __str__(self):
        return self.__class__.__name__

class PositionalEncoding2(nn.Module):
    def __init__(self, d_model, max_seq_len, dropout):
        super().__init__()
        self.d_model = d_model
        self.dropout = nn.Dropout(dropout)
        # create constant 'pe' matrix with values dependant on 
        # pos and i
        pe = torch.zeros(max_seq_len, d_model)
        for pos in range(max_seq_len):
            for i in range(0, d_model, 2):
                pe[pos, i] = \
                math.sin(pos / (10000 ** ((2 * i)/d_model)))
                pe[pos, i + 1] = \
                math.cos(pos / (10000 ** ((2 * (i + 1))/d_model)))
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)
 
    
    def forward(self, x):
        # make embeddings relatively larger
        x = x * math.sqrt(self.d_model)
        #add constant to embedding
        seq_len = x.size(1)
        pe = Variable(self.pe[:,:seq_len], requires_grad=False)
        if x.is_cuda:
            pe.cuda()
        x = x + pe
        return self.dropout(x)