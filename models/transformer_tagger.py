import torch
import torch.nn as nn
import torch.nn.functional as F

from models.transformer.encoder import TransformerEncoder
from models.softmax_output import SoftmaxOutputLayer

class TransformerTagger(nn.Module):

    encoder: TransformerEncoder
    taggingLayer: SoftmaxOutputLayer

    def __init__(self, transformerEncoder: TransformerEncoder, taggingLayer: SoftmaxOutputLayer):
        super(TransformerTagger, self).__init__()

        self.encoder = transformerEncoder
        self.taggingLayer = taggingLayer

    def forward(self, x: torch.Tensor, *args) -> torch.Tensor:
        result = self.encoder(x, *args)
        return self.taggingLayer(result) # Example CollNl2003: The output will be of size [batch_size, number_of_labels, prob_of_each_class_for_the_label]

    def predict(self, x: torch.Tensor, *args) -> torch.Tensor:
        result = self.forward(x, *args)
        _, predictions = torch.max(result, dim=-1) 
        return predictions # [batch_size, ]

