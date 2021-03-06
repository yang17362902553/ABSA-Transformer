import logging
from misc.preferences import PREFERENCES
from misc.run_configuration import default_params, hyperOpt_goodParams
from misc import utils
from misc.experimental_environment import Experiment
from misc.transfer_learning_experiment import TransferLearningExperiment
import argparse
import traceback

def run(args, parser):
	dataset_choice = args.dataset
	runs = args.runs
	epochs = args.epochs
	name = args.name
	description = args.description
	task = args.task
	use_random = args.random
	load_model_path = args.restoreModel
	produceBaseline = args.produceBaseline
	use_cuda = args.cuda


	possible_dataset_values = ['germeval', 'organic', 'coNLL-2003', 'amazon', 'transfer-amazon-organic']
	if dataset_choice not in possible_dataset_values:
		parser.error(f'The dataset argument {dataset_choice} was not in the allowed range of values: ' + str(possible_dataset_values))

	# GermEval-2017
	if dataset_choice == possible_dataset_values[0]:
		from data.germeval2017 import germeval2017_dataset as dsl
		PREFERENCES.defaults(
			data_root='./data/data/germeval2017',
			data_train='train_v1.4.tsv',    
			data_validation='dev_v1.4.tsv',
			data_test='test_TIMESTAMP2.tsv',
			source_index=0,
			target_vocab_index=2,
			file_format='csv',
			language='de'
		)
		from misc.run_configuration import good_germeval_params,OutputLayerType

		specific_hp = {**good_germeval_params, **{
			'task': task,
			'language': 'de',
			'embedding_type': 'fasttext'
		}}

	# organic-2019
	elif dataset_choice == possible_dataset_values[1]:
			from data.organic2019 import organic_dataset as dsl
			from data.organic2019 import ORGANIC_TASK_ALL, ORGANIC_TASK_ENTITIES, ORGANIC_TASK_ATTRIBUTES, ORGANIC_TASK_ENTITIES_COMBINE, ORGANIC_TASK_COARSE
			from misc.run_configuration import good_organic_hp_params

			possible_organic_values = [ORGANIC_TASK_ALL, ORGANIC_TASK_ENTITIES, ORGANIC_TASK_ATTRIBUTES, ORGANIC_TASK_ENTITIES_COMBINE, ORGANIC_TASK_COARSE]
			if task not in possible_organic_values:
				parser.error('The task argument was not in the allowed range of values: ' + str(possible_organic_values))

			PREFERENCES.defaults(
				data_root='./data/data/organic2019',
				data_train='train.csv',    
				data_validation='validation.csv',
				data_test='test.csv',
				source_index=0,
				target_vocab_index=1,
				file_format='csv',
				language='en'
			)
			specific_hp = good_organic_hp_params
			specific_hp['task'] = task		

	# coNLL-2003
	elif dataset_choice == possible_dataset_values[2]:
		PREFERENCES.defaults(

			data_root='./data/data/conll2003',
			data_train='eng.train.txt',
			data_validation='eng.testa.txt',
			data_test='eng.testb.txt',
			source_index=0,
			target_vocab_index=1,
			file_format='txt',
			language='en'
		)
		from data.conll import conll2003_dataset as dsl
		from misc.run_configuration import conll_params

		specific_hp = {**conll_params, **{
			'task': 'ner',
			'language': 'en'
		}}

	# Transfer Learning - Amazon > Organic
	elif dataset_choice == possible_dataset_values[4]:
		PREFERENCES.defaults(
			data_root=['./data/data/amazon/splits', './data/data/organic2019'],
			data_train=['train.pkl', 'train.csv'],    
			data_validation=['val.pkl', 'validation.csv'],
			data_test=['test.pkl', 'test.csv'],
			source_index=[0, 0],
			target_vocab_index=[1, 1],
			file_format=['pkl', 'csv'],
			language='en'
		)
		# PREFERENCES.defaults(
		# 	data_root=['./data/data/organic2019', './data/data/organic2019'],
		# 	data_train=['train.csv', 'train.csv'],    
		# 	data_validation=['validation.csv', 'validation.csv'],
		# 	data_test=['test.csv', 'test.csv'],
		# 	source_index=[0, 0],
		# 	target_vocab_index=[1, 1],
		# 	file_format=['csv', 'csv'],
		# 	language='en'
		# )

		from data.organic2019 import ORGANIC_TASK_COARSE
		from misc.run_configuration import good_organic_hp_params_2

		specific_hp = {**good_organic_hp_params_2, **{
			'task': task,
			'language': 'en',
			'use_spell_checkers': True
		}}
	
	# amazon reviews
	else:
		PREFERENCES.defaults(
			data_root='./data/data/amazon/splits',
			data_train='train.pkl',    
			data_validation='val.pkl',
			data_test='test.pkl',
			source_index=0,
			target_vocab_index=1,
			file_format='pkl',
			language='en'
		)
		from data.amazon import amazon_dataset as dsl
		from misc.run_configuration import hyperOpt_goodParams

		specific_hp = {**hyperOpt_goodParams, **{
			'task': 'amazon',
			'use_spell_checkers': True,
			'use_stop_words': True,
			'language': 'en',
			'clip_comments_to': 100,
			'embedding_type': 'glove'
		}}

	main_experiment_name = name
	experiment_name = utils.create_loggers(experiment_name=main_experiment_name)
	logger = logging.getLogger(__name__)
	dataset_logger = logging.getLogger('data_loader')
	logger.info('Run hyper parameter random grid search for experiment with name ' + main_experiment_name)
	logger.info('num_optim_iterations: ' + str(runs))
	specific_hp['num_epochs'] = epochs
	specific_hp['use_random_classifier'] = use_random

	try:
		logger.info('Current commit: ' + utils.get_current_git_commit())
		print('Current commit: ' + utils.get_current_git_commit())
	except Exception as err:
		logger.exception('Could not print current commit')

	try:
		if dataset_choice == possible_dataset_values[-1]:
			from data.amazon import load_splits as source_dsl
			from data.organic2019 import load_splits as targer_dsl
			e = TransferLearningExperiment(task, name, description, default_params, specific_hp, [source_dsl, targer_dsl], PREFERENCES.__dict__['prefs'], runs=runs, load_model_path=load_model_path, produce_baseline=produceBaseline)
		else:
			e = Experiment(name,  description, default_params, specific_hp, dsl, runs=runs)
		e.run()
	except Exception as err:
		logger.exception('Could not complete run')
		print('Could not complete run. The log file provides more details.')
		print(repr(err))
		traceback.print_tb(err.__traceback__)

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='HyperOpt hp optimization tool')
	parser.add_argument('dataset', type=str,
						help='Specify which dataset to optimize')
	parser.add_argument('--cuda', type=bool, default=True,
						help='Flag, wether or not cuda should be enabled. Default: If cuda is available, use it, if not then do not use it')
	parser.add_argument('--runs', type=int, default=1,
						help='Number of runs evaluation runs to perform')
	parser.add_argument('--epochs', type=int, default=35,
						help='Number of epochs to perform')
	parser.add_argument('--name', type=str, default='test',
						help='Specify a name of the optimization run')
	parser.add_argument('--description', type=str, default='test run on {} with {} epochs and {} validations',
						help='Specify a name of the optimization run')
	parser.add_argument('--task', type=str,
						help='Specify the task to execute. Only applicable when using the organic dataset')
	parser.add_argument('--random', type=bool,
						help='If random is true, use a random classifier for predictions on the dataset')

	parser.add_argument('--restoreModel', type=str, default=None,
						help='Provide a path to a checkpoint-folder which contains checkpoints. The application will search for the checkpoint with the highest score.')

	parser.add_argument('--produceBaseline', type=bool, default=False,
						help='Flag, wether or not a baseline for the transfer learning task should be trained.')

	args = parser.parse_args()

	run(args, parser)
	print('Exit')