import logging
import torch
import torch.nn as nn

from models.transformer.encoder import TransformerEncoder
from models.softmax_output import SoftmaxOutputLayerWithCommentWiseClass

class JointAspectTagger(nn.Module):
	"""description of class"""

	encoder: TransformerEncoder
	taggers: nn.ModuleList
	model_size: int
	target_size: int
	num_taggers: int
	logger: logging.RootLogger

	def __init__(self, transformerEncoder: TransformerEncoder, model_size: int, target_size: int, num_taggers: int):
		super(JointAspectTagger, self).__init__()

		assert model_size > 0
		assert target_size > 0
		assert num_taggers > 0

		self.encoder = transformerEncoder
		self.taggingLayer = taggingLayer
		self.logger = logging.getLogger('pre_training')

		self.model_size = model_size
		self.target_size = target_size
		self.num_taggers = num_taggers
		
		self.taggers = self.initialize_aspect_taggers()
		self.logger.debug(f"{self.num_taggers} initialized")
		
		self.logger.debug(f"Initilize parameters with nn.init.xavier_uniform_")
		for p in self.parameters():
			if p.dim() > 1:
				nn.init.xavier_uniform_(p)
		self.logger.debug(f"Tagger initialized")


	def initialize_aspect_taggers(self):
		taggers = []
		for i in range(self.num_taggers):
			tagger = SoftmaxOutputLayerWithCommentWiseClass(self.model_size, self.target_size, 'Apsect' + str(i))
			taggers.append(tagger)
		return nn.ModuleList(taggers)

	def forward(self, x: torch.Tensor, *args) -> torch.Tensor:
		result = self.encoder(x, *args) # result will be [batch_size, num_words, model_size]

		output: torch.Tensor = None

		# provide the result to each aspect tagger
		for _, aspect_tagger in enumerate(self.taggers):
			tagging_result = aspect_tagger(result, *args)

			if output is None:
				output = tagging_result
			else:
				output = torch.cat((output, tagging_result), 1)
		return output


