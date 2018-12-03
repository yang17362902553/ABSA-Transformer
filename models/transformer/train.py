import os
import logging
from tensorboardX import SummaryWriter
import time
import os
import shutil
from typing import Tuple, List

from misc.utils import set_seeds, torch_summarize
from misc.hyperparameters import HyperParameters

from sklearn.metrics import f1_score
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchtext
from torch.autograd import *
from tqdm.autonotebook import tqdm

DEFAULT_CHECKPOINT_PATH = ''
class Trainer(object):

    def __init__(self,
                model: nn.Module, 
                loss: nn.Module,
                optimizer: torch.optim.Optimizer,
                parameters: HyperParameters,
                data_iterators: Tuple[torchtext.data.Iterator, torchtext.data.Iterator, torchtext.data.Iterator],
                early_stopping: int,
                experiment_name: str,
                seed: int = 42,
                enable_tensorboard: bool=True,
                dummy_input: torch.Tensor=None,
                log_every_xth_iteration=-1):

        self.model = model
        self.loss = loss
        self.optimizer = optimizer
        self.parameters = parameters

        assert len(data_iterators) == 3
        self.train_iterator, self.valid_iterator, self.test_iterator = data_iterators
        self.experiment_name = experiment_name
        self.early_stopping = early_stopping

        self.checkpoint_dir = os.path.join(os.getcwd(), 'logs', experiment_name, 'checkpoints')


        self._reset()
        self.seed = seed
        self.enable_tensorboard = enable_tensorboard
        self.log_every_xth_iteration = log_every_xth_iteration

        # this logger will not print to the console. Only to the file.
        self.logger = logging.getLogger(__name__)

        # this logger will both print to the console as well as the file
        self.logger_prediction = logging.getLogger('prediction')

        model_summary = torch_summarize(self.model)

        if enable_tensorboard:
            assert dummy_input is not None

            #logdir = os.path.join(os.getcwd(), 'logs', experiment_name, 'checkpoints')
            self.tb_writer = SummaryWriter(comment=self.experiment_name)

            # for now until add graph works (needs pytorch version >= 0.4) add the model description as text
            self.tb_writer.add_text('model', model_summary, 0)
            try:
                self.tb_writer.add_graph(self.model, dummy_input, verbose=True)
            except Exception as err:
                self.logger.exception('Could not generate graph', err)
            self.logger.debug('Graph Saved')

        # TODO: initialize the rest of the trainings parameters
        # https://github.com/kolloldas/torchnlp/blob/master/torchnlp/common/train.py

    def _reset_histories(self):
        """
        Resets train and val histories for the accuracy and the loss.
        """
        self.train_loss_history = []
        self.train_acc_history = []
        self.val_acc_history = []
        self.val_loss_history = []

    def _reset(self):
        self.epoch = 0
        self.best_f1 = 0
        self.best_model_checkpoint = None
        self.early_stopping_counter = self.early_stopping
        self.loss_history = []
        self.val_loss_history = []
        self.train_acc_history = []
        self.val_acc_history = []

        self._reset_histories()

    def print_epoch_summary(self, epoch: int, iteration: int, train_loss: float, valid_loss: float, valid_f1: float):
        if epoch == 0:
            self.logger_prediction.info('# EP\t# IT\t\ttr loss\tval loss\tf1\n')
        self.logger_prediction.info('{}\t{}\t{}\t{}\t{}'.format(epoch, iteration, train_loss, valid_loss, f1_score))

    def _step(self, input: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """Make a single gradient update. This is called by train() and should not
        be called manually.
        
        Arguments:
            input {torch.Tensor} -- input batch
            target {torch.Tensor} -- targets
        
        Returns:
            torch.Tensor -- loss tensor
        """


        # Clears the gradients of all optimized :class:`torch.Tensor` s
        self.optimizer.zero_grad()

        # Compute loss and gradient
        # TODO: Provide masks
        loss = self._get_loss(input, None, target)

        # preform training step
        loss.backward()
        self.optimizer.step()

        return loss

    def _get_loss(self, input: torch.Tensor, source_mask: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """Calculates loss but does not perform gradient updates
        
        Arguments:
            input {torch.Tensor} -- Input sample
            source_mask {torch.Tensor} -- source mask
            target {torch.Tensor} -- target tensor
        
        Returns:
            torch.Tensor -- loss tensor
        """

        output = self.model(input, source_mask)
        return self.loss(output, target)

    def _get_mean_loss(self, history: List[float], iteration: int) -> float:
        losses = np.array(history[iteration - self.log_every_xth_iteration:iteration])
        return losses.mean()

    def _log_scalar(self, history: List[float], scalar_value:float, scalar_type:str, scalar_name:str, iteration:int) -> None:
        if history is not None:
            history.append(scalar_value)

        if self.enable_tensorboard:
            self.tb_writer.add_scalar('data/{}/{}'.format(scalar_type, scalar_name), scalar_value, iteration)

    def _compute_validation_losses(self) -> float:
        self.valid_iterator.init_epoch()
        losses = []
        for valid_batch in self.valid_iterator:
            x, y = valid_batch.inputs_word, valid_batch.labels
            loss = self._get_loss(x, None, y)
            losses.append(loss.item())
        return np.array(losses).mean()
        
    
    def evaluate(self, iterator: torchtext.data.Iterator) -> Tuple[float, List[float]]:
        iterator.init_epoch()

        losses = []
        f1_scores = []

        for batch in iterator:
            x, y = batch.inputs_word, batch.labels
            loss = self._get_loss(x, None, y)
            losses.append(loss.item())

            # TODO: Source mask
            source_mask = None
            prediction = self.model.predict(x, source_mask) # [batch_size, num_words] in the collnl2003 task num labels will contain the predicted class for the label
            f1Socres = self.calculate_scores(prediction, y)

            # average accuracy for batch
            batch_f1 = np.array(f1Socres).mean()
            f1_scores.append(batch_f1)

        avg_loss = np.array(losses).mean()

        f1_scores = np.array(f1_scores)
        avg_f1 = f1_scores.mean()
        return (avg_loss, avg_f1)

    def _evaluate_and_log_train(self, iteration: int) -> Tuple[float, float, float]:
        mean_train_loss = self._get_mean_loss(self.train_loss_history, iteration)
        self._log_scalar(None, mean_train_loss, 'loss', 'train/mean', iteration)

        # perform validation loss
        mean_valid_loss, mean_valid_f1 = self.evaluate(self.valid_iterator)

        # log results
        self._log_scalar(self.val_loss_history, mean_valid_loss, 'loss', 'valid/mean', iteration)
        self._log_scalar(self.val_acc_history, mean_valid_f1, 'f1', 'valid/mean', iteration)

        return (mean_train_loss, mean_valid_loss, mean_valid_f1)

    def calculate_scores(self, prediction: torch.Tensor, targets: torch.Tensor) -> List[float]:
        p_size = prediction.size()
        targets = targets.view(p_size[1], p_size[0])
        labelPredictions = prediction.view(p_size[1], p_size[0]) # transform prediction so that [num_labels, batch_size]
        result = []
        for y_pred, y_true in zip (labelPredictions, targets):
            try:
                assert y_pred.size() == y_true.size()
                assert y_true.size()[0] > 0
                f1 = f1_score(y_true, y_pred, average='macro')
            except Exception as err:
                self.logger.exception('Could not compute f1 score for input with size {} and target size {}'.format(prediction.size(), targets.size()))
            result.append(f1)

        return result


    def train(self, num_epochs: int, should_use_cuda: bool=False):

        if should_use_cuda and torch.cuda.is_available():
            self.model.cuda()
            self.logger.debug('train with cuda support')
        else:
            self.logger.debug('train without cuda support')

        # TODO: Support for early stopping
        set_seeds(self.seed)
        continue_training = True
        start_time = time.time()
        
        self.logger.info('START training.')
        iteration = 0

        for epoch in range(num_epochs):
            self.logger.debug('START Epoch {}. Current Iteration {}'.format(epoch, iteration))

            # Set up the batch generator for a new epoch
            self.train_iterator.init_epoch()
            self.epoch = epoch

            # loop iterations
            for batch in tqdm(self.train_iterator, leave=True, desc='Epoch {}'.format(epoch)): # batch is torchtext.data.batch.Batch

                if not continue_training:
                    self.logger.info('continue_training is false -> Stop training')
                    break

                iteration += 1

                # Sets the module in training mode
                self.model.train()

                x, y = batch.inputs_word, batch.labels

                train_loss = self._step(x, y)
                self._log_scalar(self.train_loss_history, train_loss.item(), 'loss', 'train', iteration)

                if iteration % self.log_every_xth_iteration == 0 and iteration > 1:
                    self._perform_iteration_evaluation(iteration)

            self.logger.info('End of Epoch {}'.format(self.epoch))
            # at the end of each epoch, check the accuracies
            mean_train_loss, mean_valid_loss, mean_valid_f1 = self._evaluate_and_log_train(iteration)
            self.print_epoch_summary(epoch, iteration, mean_train_loss, mean_valid_loss, mean_valid_f1)

            # early stopping if no improvement of val_acc during the last self.early_stopping epochs
            # https://link.springer.com/chapter/10.1007/978-3-642-35289-8_5
            if mean_valid_f1 > self.best_f1:
                self._reset_early_stopping()
            else:    
                self._perform_early_stopping()
                continue_training = False
                break


        self.logger.info('STOP training.')

        # At the end of training swap the best params into the model
        # Restore best model
        self._restore_best_model()
        self._close_tb_writer()
        self.logger.debug('Exit training')
        return self.model

    def _perform_iteration_evaluation(self, iteration: int) -> None:
        self.logger.debug('Starting evaluation in epoch {}. Current Iteration {}'.format(self.epoch, iteration))
        mean_train_loss, mean_valid_loss, mean_valid_f1 = self._evaluate_and_log_train(iteration)
        self.logger.debug('Evaluation completed')
        self.logger.info('Iteration {}'.format(iteration))
        self.logger.info('Mean train loss: {}'.format(mean_train_loss))
        self.logger.info('Mean validation loss {}'.format(mean_valid_loss))
        self.logger.info('Mean validation f1 score {}'.format(mean_valid_f1))

    def _reset_early_stopping(self, mean_valid_f1: float) -> None:
        self.logger.debug('Epoch f1 score ({}) better than last f1 score ({}). Save checkpoint'.format(mean_valid_f1, self.best_f1))
        self.best_f1 = mean_valid_f1

        # Save best model
        self.best_model_checkpoint = {
            'epoch': self.epoch,
            'state_dict': self.model.state_dict(),
            'val_acc': mean_valid_f1,
            'optimizer': self.optimizer.state_dict(),
        }
        self._save_checkpoint(iteration)

        # restore early stopping counter
        self.early_stopping_counter = self.early_stopping

    def _perform_early_stopping(self) -> None:
        self.early_stopping_counter -= 1

        # if early_stopping_counter is 0 restore best weights and stop training
        if self.early_stopping > -1 and self.early_stopping_counter <= 0:
            self.logger.info('> Early Stopping after {} epochs of no improvements.'.format(self.early_stopping))
            self.logger.info('> Restoring params of best model with validation accuracy of: {}'.format(self.best_f1))

            # Restore best model
            self._restore_best_model()

    def _close_tb_writer(self) -> None:
        if self.tb_writer is not None:
            self.logger.debug('Try to write scalars file and close tensorboard writer')
            try:
                self.tb_writer.export_scalars_to_json(os.path.join(os.getcwd(), 'logs', self.experiment_name, "model_all_scalars.json"))
            except Exception as err:
                self.logger.exception('TensorboardX could not save scalar json values', err)
            finally:
                self.tb_writer.close()

    def _restore_best_model(self) -> None:
        try:
            self.model.load_state_dict(self.best_model_checkpoint['state_dict'])
        except Exception as err:
            self.logger.exception('Could not restore best model ', err)

        try:
            self.optimizer.load_state_dict(self.best_model_checkpoint['optimizer'])
        except Exception as err:
            self.logger.exception('Could not restore best model ', err)

        self.logger.info('Best model parameters were at \nIteration {}\nValidation f1 score {}'
                            .format(self.best_model_checkpoint['epoch'], self.best_model_checkpoint['val_acc']))

    def _save_checkpoint(self, iteration: int) -> None:
        self.logger.debug('Saving model... ' + self.checkpoint_dir)

        checkpoint = {
            'iteration': iteration,
            'state_dict': self.model.state_dict(),
            'optimizer': self.optimizer.state_dict(),
        }

        filename = 'checkpoint_{}.data'.format(iteration)
        try:
            torch.save(checkpoint, os.path.join(self.checkpoint_dir, filename))
            #shutil.copyfile(filename, os.path.join(self.checkpoint_dir, filename))
        except Exception as err:
            self.logger.exception('Could not save model.', err)